"""Unit tests for role ordering and auth guard logic in cartlog.web.guards."""

from starlette.requests import Request

from cartlog.db.models import Role
from cartlog.web.guards import AuthRedirect, _login_redirect, role_satisfies


def test_role_ordering():
    """Verify the role hierarchy: ADMIN > EDITOR > VIEWER."""
    assert role_satisfies(Role.ADMIN, Role.VIEWER) is True
    assert role_satisfies(Role.EDITOR, Role.EDITOR) is True
    assert role_satisfies(Role.VIEWER, Role.EDITOR) is False
    assert role_satisfies(Role.EDITOR, Role.ADMIN) is False


def test_login_redirect_preserves_and_encodes_query():
    """Verify the login redirect preserves the full path plus query string, percent-encoded."""
    # Given a request to /receipts?store=costco
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/receipts",
        "query_string": b"store=costco",
        "headers": [],
    }

    # When building the login redirect
    exc = _login_redirect(Request(scope))

    # Then the redirect is an AuthRedirect with the full path encoded in next=
    assert isinstance(exc, AuthRedirect)
    assert exc.location.startswith("/login?next=")
    assert "%2Freceipts" in exc.location
    assert "store" in exc.location
    assert "costco" in exc.location


def test_login_redirect_no_query():
    """Verify the login redirect works correctly for paths with no query string."""
    # Given a request with no query string
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/dashboard",
        "query_string": b"",
        "headers": [],
    }

    # When building the login redirect
    exc = _login_redirect(Request(scope))

    # Then the redirect encodes only the path
    assert isinstance(exc, AuthRedirect)
    assert exc.location == "/login?next=%2Fdashboard"
