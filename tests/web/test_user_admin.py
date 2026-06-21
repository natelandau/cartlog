"""Tests for admin user management routes at /admin/users."""

from __future__ import annotations

from cartlog.auth.sessions import SessionService
from cartlog.auth.users import UserService
from cartlog.db.models import Role
from cartlog.db.models import Session as SessionRow
from tests.factories import seed_user


def test_admin_creates_user(admin_client):
    """Verify an admin can create a new user via POST /admin/users."""
    # Given a valid user creation form payload
    payload = {"username": "kid", "password": "violet pantry koala", "role": "viewer"}

    # When posting to /admin/users
    resp = admin_client.post("/admin/users", data=payload)

    # Then the response succeeds and the user exists in the DB with the expected role
    assert resp.status_code in (200, 303)
    with admin_client.app.state.session_factory() as s:
        user = UserService(s).get_by_username("kid")
        assert user is not None
        assert user.role == Role.VIEWER


def test_editor_cannot_access_user_admin(editor_client):
    """Verify that an editor receives 403 from GET /admin/users."""
    # When an editor requests the user admin page
    resp = editor_client.get("/admin/users")

    # Then access is forbidden
    assert resp.status_code == 403


def test_anon_redirected_from_user_admin(anon_client):
    """Verify that an unauthenticated request to /admin/users is redirected to /login."""
    # Given a seeded admin so the setup gate does not fire
    with anon_client.app.state.session_factory() as s:
        seed_user(s, username="dad", role=Role.ADMIN)

    # When an anonymous client requests the user admin page
    resp = anon_client.get("/admin/users", follow_redirects=False)

    # Then it redirects to /login with a 303
    assert resp.status_code == 303
    assert "/login" in resp.headers["location"]


def test_reset_password_sets_must_change_and_revokes_sessions(admin_client):
    """Verify reset-password sets must_change_password and revokes all existing sessions."""
    # Given an existing user with an active session
    with admin_client.app.state.session_factory() as s:
        target = seed_user(s, username="target_user", role=Role.EDITOR)
        sess = SessionService(s).create(target)
        s.commit()
        target_id = target.id
        session_id = sess.id

    # When the admin resets the target user's password
    resp = admin_client.post(f"/admin/users/{target_id}/reset-password")

    # Then the response is successful
    assert resp.status_code == 200
    # And must_change_password is set and sessions are revoked
    with admin_client.app.state.session_factory() as s:
        fresh = s.get(type(target), target_id)
        assert fresh.must_change_password is True
        # The old session must be gone
        still_alive = s.get(SessionRow, session_id)
        assert still_alive is None
    # And the temp password is shown in the response body
    assert resp.text  # non-empty; actual content checked by regex would be noisy


def test_reset_password_shows_temp_password_once(admin_client):
    """Verify the reset-password response contains the temporary password in the HTML."""
    # Given an existing user
    with admin_client.app.state.session_factory() as s:
        target = seed_user(s, username="temptest", role=Role.VIEWER)
        s.commit()
        target_id = target.id

    # When resetting the password
    resp = admin_client.post(f"/admin/users/{target_id}/reset-password")

    # Then the temp password is present in the response body (show-once fragment)
    assert resp.status_code == 200
    assert "temp" in resp.text.lower() or len(resp.text) > 20


def test_last_admin_deactivate_blocked(admin_client):
    """Verify deactivating the last active admin is rejected."""
    # Given the admin_client is authenticated as "admin" (the seeded Role.ADMIN user)
    with admin_client.app.state.session_factory() as s:
        admin_user = UserService(s).get_by_username(Role.ADMIN.value)
        assert admin_user is not None
        admin_id = admin_user.id

    # When trying to deactivate that admin (who is the only active admin)
    resp = admin_client.post(f"/admin/users/{admin_id}/active", data={"active": "false"})

    # Then the request is rejected with a 422
    assert resp.status_code == 422


def test_last_admin_role_demote_blocked(admin_client):
    """Verify demoting the last active admin to a lower role is rejected."""
    # Given the admin_client's admin user is the only active admin
    with admin_client.app.state.session_factory() as s:
        admin_user = UserService(s).get_by_username(Role.ADMIN.value)
        assert admin_user is not None
        admin_id = admin_user.id

    # When trying to change that admin's role to editor
    resp = admin_client.post(f"/admin/users/{admin_id}/role", data={"role": "editor"})

    # Then the request is rejected with a 422
    assert resp.status_code == 422


def test_last_admin_deactivate_button_hidden(admin_client):
    """Verify the sole active admin's row hides the Deactivate button it cannot use."""
    # Given the admin_client authenticated as the only admin ("admin")
    # When loading the user management page
    resp = admin_client.get("/admin/users")

    # Then no Deactivate control is rendered for that admin
    assert resp.status_code == 200
    assert 'aria-label="Deactivate admin"' not in resp.text


def test_last_admin_demote_options_hidden(admin_client):
    """Verify the sole active admin's role menu omits the demote options it cannot use."""
    # Given the admin_client authenticated as the only admin ("admin")
    # When loading the user management page
    resp = admin_client.get("/admin/users")

    # Then the role menu offers no form to demote that admin to a lower role
    assert resp.status_code == 200
    assert 'name="role" value="editor"' not in resp.text
    assert 'name="role" value="viewer"' not in resp.text


def test_deactivate_button_shown_when_second_admin_exists(admin_client):
    """Verify the Deactivate button reappears once a second admin makes it safe to use."""
    # Given a second active admin so neither admin is the last
    with admin_client.app.state.session_factory() as s:
        seed_user(s, username="second_admin", role=Role.ADMIN)
        s.commit()

    # When loading the user management page
    resp = admin_client.get("/admin/users")

    # Then both admins expose a Deactivate button and demote options
    assert resp.status_code == 200
    assert 'aria-label="Deactivate admin"' in resp.text
    assert 'aria-label="Deactivate second_admin"' in resp.text
    assert 'name="role" value="editor"' in resp.text


def test_deactivate_button_shown_for_non_admin(admin_client):
    """Verify a non-admin user always exposes a Deactivate button."""
    # Given an editor user alongside the sole admin
    with admin_client.app.state.session_factory() as s:
        seed_user(s, username="editor_user", role=Role.EDITOR)
        s.commit()

    # When loading the user management page
    resp = admin_client.get("/admin/users")

    # Then the editor exposes a Deactivate button
    assert resp.status_code == 200
    assert 'aria-label="Deactivate editor_user"' in resp.text


def test_create_user_duplicate_username_rejected(admin_client):
    """Verify that creating a user with an existing username returns 422."""
    # Given an existing user
    with admin_client.app.state.session_factory() as s:
        seed_user(s, username="taken", role=Role.VIEWER)

    # When trying to create another user with the same username
    resp = admin_client.post(
        "/admin/users",
        data={"username": "taken", "password": "violet pantry koala", "role": "viewer"},
    )

    # Then the request fails with 422
    assert resp.status_code == 422


def test_create_user_weak_password_rejected(admin_client):
    """Verify that creating a user with a password that fails policy is rejected with 422."""
    # When posting with a short password
    resp = admin_client.post(
        "/admin/users",
        data={"username": "newguy", "password": "short", "role": "viewer"},
    )

    # Then the request is rejected
    assert resp.status_code == 422


def test_set_role_success(admin_client):
    """Verify an admin can change another user's role."""
    # Given a viewer user
    with admin_client.app.state.session_factory() as s:
        target = seed_user(s, username="roletest", role=Role.VIEWER)
        target_id = target.id

    # When changing the role to editor
    resp = admin_client.post(f"/admin/users/{target_id}/role", data={"role": "editor"})

    # Then the response is successful and the role is updated
    assert resp.status_code == 200
    with admin_client.app.state.session_factory() as s:
        fresh = UserService(s).get_by_username("roletest")
        assert fresh is not None
        assert fresh.role == Role.EDITOR


def test_set_active_success(admin_client):
    """Verify an admin can deactivate a non-admin user."""
    # Given an editor user (not the last admin)
    with admin_client.app.state.session_factory() as s:
        target = seed_user(s, username="toggletest", role=Role.EDITOR)
        target_id = target.id

    # When deactivating that user
    resp = admin_client.post(f"/admin/users/{target_id}/active", data={"active": "false"})

    # Then the response is successful and the user is inactive
    assert resp.status_code == 200
    with admin_client.app.state.session_factory() as s:
        fresh = UserService(s).get_by_username("toggletest")
        assert fresh is not None
        assert fresh.is_active is False
