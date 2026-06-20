"""Tests for the first-run setup wizard."""

from __future__ import annotations

from cartlog.auth.app_config import AppConfigService
from cartlog.auth.users import UserService
from cartlog.db.models import Role
from cartlog.web.middleware import CSRF_COOKIE
from cartlog.web.security import make_csrf_token
from tests.factories import seed_user

# Must match the CARTLOG_SECRET_KEY set by the autouse _test_secret_key fixture.
_TEST_SECRET_KEY = "test-secret-key-0123456789abcdef"  # noqa: S105


def _post_setup_account(
    client,
    *,
    username: str,
    password: str,
    confirm: str,
    name: str = "",
    follow_redirects: bool = False,
):
    """Submit the account-creation form with a valid CSRF token via header.

    The anon_client does not auto-inject CSRF (only _AuthClient does), so
    inject the double-submit cookie + header explicitly -- the same approach
    the login tests use via test_login._login().

    Args:
        client: The TestClient to use.
        username: The login handle to submit.
        password: The plaintext password to submit.
        confirm: The password confirmation.
        name: Optional display name.
        follow_redirects: Whether the client should follow 3xx responses.

    Returns:
        The HTTP response from POST /setup/account.
    """
    token = make_csrf_token(_TEST_SECRET_KEY)
    client.cookies.set(CSRF_COOKIE, token)
    return client.post(
        "/setup/account",
        data={"username": username, "password": password, "confirm": confirm, "name": name},
        headers={"x-csrf-token": token},
        follow_redirects=follow_redirects,
    )


def test_setup_visible_when_no_users(anon_client):
    """Verify /setup returns 200 and the expected heading when no users exist."""
    # Given a fresh database with no users (anon_client seeds receipts + app_config, no users)
    with anon_client.app.state.session_factory() as s:
        assert UserService(s).count() == 0

    # When the setup page is requested
    resp = anon_client.get("/setup")

    # Then it renders with the setup copy
    assert resp.status_code == 200
    assert "set up cartlog" in resp.text.lower()


def test_setup_creates_admin_and_logs_in(anon_client):
    """Verify POST /setup/account creates the first admin and returns the access step."""
    # Given no users in the database
    with anon_client.app.state.session_factory() as s:
        assert UserService(s).count() == 0

    # When the account form is submitted with valid data (follow_redirects=False to
    # inspect the session cookie before any redirect strips the Set-Cookie header)
    resp = _post_setup_account(
        anon_client,
        username="dad",
        password="violet pantry koala",
        confirm="violet pantry koala",
        follow_redirects=False,
    )

    # Then the response returns the access step (200 htmx swap)
    assert resp.status_code in (200, 303)

    # And a session cookie is set in the response
    set_cookie = resp.headers.get("set-cookie", "")
    assert "cartlog_session" in set_cookie or "__Host-cartlog_session" in set_cookie

    # And the admin user is created in the database
    with anon_client.app.state.session_factory() as s:
        user = UserService(s).get_by_username("dad")
        assert user is not None
        assert user.role == Role.ADMIN


def test_setup_locked_after_admin_exists(app_client):
    """Verify /setup redirects to / when an admin already exists."""
    # Given the app_client fixture which seeds an admin user
    resp = app_client.get("/setup", follow_redirects=False)

    # Then the setup route is locked and redirects away
    assert resp.status_code in (303, 302)


def test_setup_account_locked_after_admin_exists(app_client):
    """Verify POST /setup/account redirects to / when an admin already exists."""
    # Given the app_client fixture which seeds an admin user
    resp = app_client.post(
        "/setup/account",
        data={
            "username": "new_user",
            "password": "violet pantry koala",
            "confirm": "violet pantry koala",
        },
        follow_redirects=False,
    )

    # Then the endpoint is locked and redirects away
    assert resp.status_code in (303, 302)


def test_setup_access_locked_after_admin_exists(anon_client):
    """Verify POST /setup/access rejects a non-admin caller and does not mutate config.

    After an admin is created, only an authenticated admin may set the posture. An
    unauthenticated (anonymous) caller must be redirected to / without any change.
    """
    # Given an admin already exists and allow_anonymous_read defaults to True
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="existing_admin", role=Role.ADMIN)
        assert AppConfigService(s).allow_anonymous_read() is True

    # When an unauthenticated caller POSTs to /setup/access to flip the posture
    token = make_csrf_token(_TEST_SECRET_KEY)
    anon_client.cookies.set(CSRF_COOKIE, token)
    resp = anon_client.post(
        "/setup/access",
        data={"posture": "private"},
        headers={"x-csrf-token": token},
        follow_redirects=False,
    )

    # Then the endpoint redirects to / without changing the setting
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/"

    with anon_client.app.state.session_factory() as s:
        assert AppConfigService(s).allow_anonymous_read() is True


def test_setup_account_validation_mismatched_passwords(anon_client):
    """Verify POST /setup/account returns 422 with an error on mismatched passwords."""
    # Given no users, but mismatched passwords
    resp = _post_setup_account(
        anon_client,
        username="dad",
        password="violet pantry koala",
        confirm="wrong passphrase here",
    )

    # Then the response is 422 with the mismatch error
    # The apostrophe is HTML-escaped in the template response as &#39;
    assert resp.status_code == 422
    assert "match" in resp.text.lower()


def test_setup_account_password_too_short(anon_client):
    """Verify POST /setup/account returns 422 when password is too short."""
    # Given a password that is too short (under 12 chars)
    resp = _post_setup_account(
        anon_client,
        username="dad",
        password="short",
        confirm="short",
    )

    # Then the response is 422 with the minimum-length policy message
    assert resp.status_code == 422
    assert "12" in resp.text or "character" in resp.text.lower()


def _apply_session_cookie_from_response(client, response) -> None:
    """Copy the session cookie from a response into the client's cookie jar.

    setup_account sets the session cookie via response.set_cookie() with a Secure
    attribute. The TestClient runs over plain HTTP and httpx will not resend Secure
    cookies to http://testserver. Copying the cookie value manually under the plain
    'cartlog_session' name (which _cookie_value() in auth.py also checks) lets the
    next request carry the authenticated session without requiring HTTPS in tests.

    Args:
        client: The TestClient whose cookie jar to update.
        response: The response that contains the Set-Cookie header.
    """
    set_cookie = response.headers.get("set-cookie", "")
    # Extract the session id from the Set-Cookie header value (first ;-delimited part).
    for raw_part in set_cookie.split(";"):
        segment = raw_part.strip()
        if "=" in segment and "cartlog_session" in segment:
            _name, _, value = segment.partition("=")
            client.cookies.set("cartlog_session", value)
            return


def test_setup_access_private_posture_persisted_after_account_creation(anon_client):
    """Verify POST /setup/access persists 'private' posture when called by the just-created admin.

    Mirrors the real wizard flow: account step first (creating the admin and setting the
    session cookie), then access step using that authenticated session.
    """
    # Given no users and allow_anonymous_read starts True
    with anon_client.app.state.session_factory() as s:
        assert UserService(s).count() == 0
        assert AppConfigService(s).allow_anonymous_read() is True

    # When the account step is submitted and the admin is created (session cookie is set)
    account_resp = _post_setup_account(
        anon_client,
        username="dad",
        password="violet pantry koala",
        confirm="violet pantry koala",
        follow_redirects=False,
    )
    assert account_resp.status_code in (200, 303), (
        f"Account step failed with {account_resp.status_code}"
    )
    # Copy the session cookie into the client jar under the plain name so it round-trips
    # over plain HTTP (TestClient does not send Secure cookies to http://testserver).
    _apply_session_cookie_from_response(anon_client, account_resp)

    # And the access step is submitted with "private" using the authenticated session
    token = make_csrf_token(_TEST_SECRET_KEY)
    anon_client.cookies.set(CSRF_COOKIE, token)
    access_resp = anon_client.post(
        "/setup/access",
        data={"posture": "private"},
        headers={"x-csrf-token": token},
        follow_redirects=False,
    )

    # Then the done panel is returned and allow_anonymous_read is False
    assert access_resp.status_code == 200, (
        f"Expected 200 but got {access_resp.status_code} - setup_access guard is blocking admin"
    )
    assert "dashboard" in access_resp.text.lower()

    with anon_client.app.state.session_factory() as s:
        assert AppConfigService(s).allow_anonymous_read() is False


def test_setup_access_open_posture_persisted_after_account_creation(anon_client):
    """Verify POST /setup/access persists 'open' posture when called by the just-created admin."""
    # Given no users and allow_anonymous_read starts True
    with anon_client.app.state.session_factory() as s:
        assert UserService(s).count() == 0

    # When the account step creates the admin
    account_resp = _post_setup_account(
        anon_client,
        username="dad",
        password="violet pantry koala",
        confirm="violet pantry koala",
        follow_redirects=False,
    )
    assert account_resp.status_code in (200, 303)
    # Copy the session cookie so the next request is authenticated.
    _apply_session_cookie_from_response(anon_client, account_resp)

    # And the access step is submitted with "open"
    token = make_csrf_token(_TEST_SECRET_KEY)
    anon_client.cookies.set(CSRF_COOKIE, token)
    access_resp = anon_client.post(
        "/setup/access",
        data={"posture": "open"},
        headers={"x-csrf-token": token},
        follow_redirects=False,
    )

    # Then allow_anonymous_read remains True
    assert access_resp.status_code == 200
    with anon_client.app.state.session_factory() as s:
        assert AppConfigService(s).allow_anonymous_read() is True


def test_setup_access_blocked_for_anonymous_after_admin_exists(anon_client):
    """Verify POST /setup/access rejects an unauthenticated caller when an admin exists."""
    # Given an admin already exists but the caller has no session cookie
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="existing_admin", role=Role.ADMIN)

    # When an anonymous caller posts to /setup/access
    token = make_csrf_token(_TEST_SECRET_KEY)
    anon_client.cookies.set(CSRF_COOKIE, token)
    resp = anon_client.post(
        "/setup/access",
        data={"posture": "private"},
        headers={"x-csrf-token": token},
        follow_redirects=False,
    )

    # Then the request is rejected (redirect to /) without changing the setting
    assert resp.status_code == 303
    assert resp.headers.get("location") == "/"

    with anon_client.app.state.session_factory() as s:
        assert AppConfigService(s).allow_anonymous_read() is True
