"""RBAC matrix tests: verify every route enforces the correct access level.

The test grid covers four principals (anon, viewer, editor, admin) against three
access tiers (read, editor-only, admin-only).  The app is seeded with
allow_anonymous_read=True (the default), so read routes accept unauthenticated
visitors.
"""

from __future__ import annotations

import pytest

from tests.web.helpers import first_line_item_id, first_receipt_id

# ---------------------------------------------------------------------------
# Route tables
# ---------------------------------------------------------------------------

# Routes that require at least read access.  With allow_anonymous_read=True
# (the test default) all four principals should get 200.
READ_ROUTES = [
    "/",
    "/receipts",
    "/search",
    "/charts",
    "/jobs",
    "/categories",
    "/api/analytics/price-history?product=eggs",
    "/api/analytics/search?q=eggs",
    "/export",
]

# Routes that require at least Editor role.
# - admin/editor: 200
# - viewer: 403
# - anon: 302 redirect to /login (AuthRedirect exception is converted to 302)
EDITOR_ROUTES = [
    "/upload",
]

# Routes that require Admin role.
# - admin: 200
# - editor/viewer: 403
# - anon: 302 redirect to /login
ADMIN_ROUTES = [
    "/admin",
    "/admin/products",
    "/admin/stores",
    "/admin/store-merges",
    "/admin/transformations",
    "/admin/settings",
    "/admin/integrations",
    "/admin/users",
]


# ---------------------------------------------------------------------------
# Read-route matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", READ_ROUTES)
def test_read_routes_allow_admin(admin_client, path):
    """Verify read routes return 200 for the admin role."""
    assert admin_client.get(path).status_code == 200


@pytest.mark.parametrize("path", READ_ROUTES)
def test_read_routes_allow_editor(editor_client, path):
    """Verify read routes return 200 for the editor role."""
    assert editor_client.get(path).status_code == 200


@pytest.mark.parametrize("path", READ_ROUTES)
def test_read_routes_allow_viewer(viewer_client, path):
    """Verify read routes return 200 for the viewer role."""
    assert viewer_client.get(path).status_code == 200


@pytest.mark.parametrize("path", READ_ROUTES)
def test_read_routes_allow_anon_when_public_read_is_on(anon_client, path):
    """Verify read routes return 200 for unauthenticated visitors when public read is on.

    The test database is seeded with allow_anonymous_read=True (the default),
    so require_read lets anonymous requests through.
    """
    assert anon_client.get(path).status_code == 200


# ---------------------------------------------------------------------------
# Editor-route matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", EDITOR_ROUTES)
def test_editor_routes_allow_admin(admin_client, path):
    """Verify editor-tier routes return 200 for the admin role."""
    assert admin_client.get(path).status_code == 200


@pytest.mark.parametrize("path", EDITOR_ROUTES)
def test_editor_routes_allow_editor(editor_client, path):
    """Verify editor-tier routes return 200 for the editor role."""
    assert editor_client.get(path).status_code == 200


@pytest.mark.parametrize("path", EDITOR_ROUTES)
def test_editor_routes_forbid_viewer(viewer_client, path):
    """Verify editor-tier routes return 403 for the viewer role."""
    assert viewer_client.get(path).status_code == 403


@pytest.mark.parametrize("path", EDITOR_ROUTES)
def test_editor_routes_redirect_anon(anon_client, path):
    """Verify editor-tier routes redirect unauthenticated visitors to login.

    The app exception handler converts AuthRedirect to a 303; follow_redirects=False
    lets us inspect the redirect response rather than following it to the login page.
    """
    resp = anon_client.get(path, follow_redirects=False)
    assert resp.status_code in (302, 303)


# ---------------------------------------------------------------------------
# Admin-route matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_admin_routes_allow_admin(admin_client, path):
    """Verify admin routes return 200 for the admin role."""
    assert admin_client.get(path).status_code == 200


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_admin_routes_forbid_editor(editor_client, path):
    """Verify admin routes return 403 for the editor role."""
    assert editor_client.get(path).status_code == 403


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_admin_routes_forbid_viewer(viewer_client, path):
    """Verify admin routes return 403 for the viewer role."""
    assert viewer_client.get(path).status_code == 403


@pytest.mark.parametrize("path", ADMIN_ROUTES)
def test_admin_routes_redirect_anon(anon_client, path):
    """Verify admin routes redirect unauthenticated visitors to login.

    The app exception handler converts AuthRedirect to a 303 (303 See Other).
    """
    resp = anon_client.get(path, follow_redirects=False)
    assert resp.status_code in (302, 303)


# ---------------------------------------------------------------------------
# Mutation-under-read fix: analytics POST and editor-only edit GET routes
# ---------------------------------------------------------------------------


def test_search_item_save_forbids_viewer(viewer_client, tmp_path) -> None:
    """Verify POST /search/items/{id} returns 403 for a viewer (data mutation must require editor)."""
    # Given a real line item id from the seeded database
    line_id = first_line_item_id(viewer_client)

    # When a viewer tries to save an edit
    resp = viewer_client.post(
        f"/search/items/{line_id}",
        data={"canonical_name": "eggs", "category_id": ""},
    )

    # Then the request is forbidden
    assert resp.status_code == 403


def test_search_item_save_redirects_anon(anon_client, tmp_path) -> None:
    """Verify POST /search/items/{id} redirects an unauthenticated visitor to login."""
    # Given a real line item id; anon_client shares the same seeded db so we
    # derive the id by querying through a viewer_client on the same engine.
    # The anon_client fixture creates its own db, so use a numeric id that will
    # be seeded (1 is always present when the factory seeds receipts).
    # We cannot call first_line_item_id(anon_client) because anon_client is a
    # plain TestClient without the CSRF auto-injection; use id=1 defensively,
    # and if the line doesn't exist the guard fires first anyway.
    resp = anon_client.post(
        "/search/items/1",
        data={"canonical_name": "eggs", "category_id": ""},
        follow_redirects=False,
    )

    # Then the unauthenticated visitor is sent to login
    assert resp.status_code in (302, 303)


def test_search_item_save_passes_guard_for_editor(editor_client) -> None:
    """Verify POST /search/items/{id} passes the auth guard for an editor (200 or 422)."""
    # Given a real line item id
    line_id = first_line_item_id(editor_client)

    # When an editor posts a valid edit
    resp = editor_client.post(
        f"/search/items/{line_id}",
        data={"canonical_name": "eggs renamed", "category_id": ""},
    )

    # Then the guard passes (200 success or 422 validation error; NOT 403/302)
    assert resp.status_code not in (302, 303, 403)


def test_search_item_edit_forbids_viewer(viewer_client) -> None:
    """Verify GET /search/items/{id}/edit returns 403 for a viewer (edit form is editor-only)."""
    # Given a real line item id
    line_id = first_line_item_id(viewer_client)

    # When a viewer requests the edit form
    resp = viewer_client.get(f"/search/items/{line_id}/edit")

    # Then the request is forbidden
    assert resp.status_code == 403


def test_search_item_edit_allows_editor(editor_client) -> None:
    """Verify GET /search/items/{id}/edit returns 200 for an editor."""
    # Given a real line item id
    line_id = first_line_item_id(editor_client)

    # When an editor requests the edit form
    resp = editor_client.get(f"/search/items/{line_id}/edit")

    # Then the form renders
    assert resp.status_code == 200


def test_receipt_edit_forbids_viewer(viewer_client) -> None:
    """Verify GET /receipts/{id}/edit returns 403 for a viewer (edit panel is editor-only)."""
    # Given a real receipt id
    receipt_id = first_receipt_id(viewer_client)

    # When a viewer requests the edit panel
    resp = viewer_client.get(f"/receipts/{receipt_id}/edit")

    # Then the request is forbidden
    assert resp.status_code == 403


def test_receipt_edit_allows_editor(editor_client) -> None:
    """Verify GET /receipts/{id}/edit returns 200 for an editor."""
    # Given a real receipt id
    receipt_id = first_receipt_id(editor_client)

    # When an editor requests the edit panel
    resp = editor_client.get(f"/receipts/{receipt_id}/edit")

    # Then the panel renders
    assert resp.status_code == 200
