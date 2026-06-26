"""Tests for the login, logout, and open-redirect guard."""

from __future__ import annotations

import pytest

from cartlog.db.models import Role
from cartlog.web.middleware import CSRF_COOKIE
from cartlog.web.routers.auth_routes import _safe_next
from cartlog.web.security import make_csrf_token
from tests.conftest import TEST_SECRET_KEY as _TEST_SECRET_KEY
from tests.factories import seed_user


def _login(client, *, username: str, password: str, follow_redirects: bool = True):
    """Submit the login form with a valid CSRF token.

    The CSRF middleware requires the double-submit cookie pattern: a valid token must be
    present in both the cookie and the form body. The anon_client does not auto-inject
    CSRF (only _AuthClient does), so we set it explicitly here.

    Args:
        client: The TestClient to use.
        username: The login handle to submit.
        password: The plaintext password to submit.
        follow_redirects: Whether the client should follow 3xx responses.

    Returns:
        The HTTP response from POST /login.
    """
    token = make_csrf_token(_TEST_SECRET_KEY)
    client.cookies.set(CSRF_COOKIE, token)
    # Send the CSRF token in the header so the middleware does not consume the request body
    # looking for it in the form. Consuming the body in middleware prevents FastAPI's Form()
    # from reading it later, causing 422 "field required" errors on the route parameters.
    return client.post(
        "/login",
        data={"username": username, "password": password},
        headers={"x-csrf-token": token},
        follow_redirects=follow_redirects,
    )


def test_login_success_sets_cookie_and_redirects(anon_client):
    """Verify a valid login creates a session cookie and redirects with 303."""
    # Given a seeded user
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="dad", role=Role.ADMIN, password="violet pantry koala")

    # When the user submits valid credentials
    resp = _login(
        anon_client, username="dad", password="violet pantry koala", follow_redirects=False
    )

    # Then a session cookie is set and the client is redirected
    assert resp.status_code == 303
    assert any("session" in c for c in resp.cookies) or "set-cookie" in resp.headers


def test_login_bad_credentials_shows_generic_error(anon_client):
    """Verify bad credentials re-render the login form with a generic error at 422."""
    # Given a seeded user
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="dad", role=Role.ADMIN)

    # When the user submits the wrong password
    resp = _login(anon_client, username="dad", password="nope")

    # Then the login page is re-rendered with a generic error message
    assert resp.status_code in (200, 422)
    assert "match" in resp.text.lower()


def test_logout_clears_session(admin_client):
    """Verify logout revokes the session and redirects to the login page."""
    # When the authenticated user posts to logout
    resp = admin_client.post("/logout", follow_redirects=False)

    # Then they are redirected (303) toward /login
    assert resp.status_code == 303


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # absolute external URLs are rejected
        ("https://evil.com", "/"),
        ("http://evil.com/steal", "/"),
        # protocol-relative URLs (open redirect via //) are rejected
        ("//evil.com", "/"),
        ("//evil.com/path", "/"),
        # valid local paths pass through unchanged
        ("/dashboard", "/dashboard"),
        ("/receipts?page=2", "/receipts?page=2"),
        ("/", "/"),
        # backslash-prefixed paths (Windows open-redirect vector) are rejected
        ("/\\evil.com", "/"),
    ],
)
def test_safe_next(value, expected):
    """Verify _safe_next keeps safe local paths and rejects open-redirect vectors to '/'."""
    assert _safe_next(value) == expected


def test_login_browser_form_post_without_header(anon_client):
    """Verify a native browser form POST with csrf_token in the body (no x-csrf-token header) succeeds.

    Regression test for the BaseHTTPMiddleware body-read bug: CsrfMiddleware previously
    called request.form() in middleware, which drained the receive channel and caused the
    endpoint to see an empty form body, returning 403. The fix moves validation into a
    FastAPI dependency so the body is cached and visible to both the CSRF check and the
    login route.
    """
    # Given a seeded admin user and a valid CSRF cookie obtained from GET /login
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="browser_user", role=Role.ADMIN, password="violet pantry koala")

    # Obtain the CSRF cookie by visiting the login page first
    get_resp = anon_client.get("/login")
    assert get_resp.status_code == 200
    csrf_cookie = anon_client.cookies.get(CSRF_COOKIE)
    assert csrf_cookie is not None, "GET /login must set the cartlog_csrf cookie"

    # When the user submits the login form WITHOUT the x-csrf-token header (browser flow),
    # sending the CSRF token only in the form body
    resp = anon_client.post(
        "/login",
        data={
            "username": "browser_user",
            "password": "violet pantry koala",
            "csrf_token": csrf_cookie,
            "next": "/",
        },
        follow_redirects=False,
    )

    # Then the login succeeds with a redirect, not a 403
    assert resp.status_code == 303, (
        f"Expected 303 redirect but got {resp.status_code}. "
        "If 403: CSRF body-read bug is still present. "
        "If 422: form fields not reaching the endpoint."
    )


def test_login_rejects_missing_csrf_token(anon_client):
    """Verify a POST with a missing CSRF token returns 403."""
    # Given a seeded user
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="csrf_victim", role=Role.ADMIN, password="violet pantry koala")

    # When the user posts without any CSRF token (no cookie, no header, no form field)
    resp = anon_client.post(
        "/login",
        data={"username": "csrf_victim", "password": "violet pantry koala"},
        follow_redirects=False,
    )

    # Then the request is rejected with 403
    assert resp.status_code == 403


def test_login_rejects_wrong_csrf_token(anon_client):
    """Verify a POST with a mismatched CSRF token returns 403."""
    # Given a seeded user and a valid CSRF cookie
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="csrf_victim2", role=Role.ADMIN, password="violet pantry koala")

    get_resp = anon_client.get("/login")
    assert get_resp.status_code == 200
    csrf_cookie = anon_client.cookies.get(CSRF_COOKIE)
    assert csrf_cookie is not None

    # When the user submits a form with a WRONG token in the body (not matching the cookie)
    resp = anon_client.post(
        "/login",
        data={
            "username": "csrf_victim2",
            "password": "violet pantry koala",
            "csrf_token": "this-is-not-the-right-token",
        },
        follow_redirects=False,
    )

    # Then the request is rejected with 403
    assert resp.status_code == 403


def test_login_form_propagates_next_param(anon_client):
    """Verify GET /login?next=/receipts renders a hidden field with value /receipts."""
    # Given an admin exists so the setup gate does not redirect /login to /setup
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="admin", role=Role.ADMIN)

    # When the login page is loaded with a ?next= query parameter
    resp = anon_client.get("/login?next=/receipts")

    # Then the rendered form contains the next value in the hidden field
    assert resp.status_code == 200
    assert 'value="/receipts"' in resp.text or "value=%22/receipts%22" in resp.text


def test_login_form_guards_open_redirect_in_next_param(anon_client):
    """Verify GET /login?next=https://evil.com renders next as '/' not the external URL."""
    # Given an admin exists so the setup gate does not redirect /login to /setup
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="admin", role=Role.ADMIN)

    # When the login page is loaded with an external URL as next
    resp = anon_client.get("/login?next=https://evil.com")

    # Then the hidden field falls back to '/' to block the open redirect
    assert resp.status_code == 200
    assert "https://evil.com" not in resp.text
    assert 'value="/"' in resp.text
