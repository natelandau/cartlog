"""Tests that auth cookies are only marked Secure over HTTPS.

A Secure cookie set over plain HTTP is dropped by Safari and never returned by any browser,
which silently breaks CSRF/login. cookie_is_secure caps the configured flag by request scheme.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cartlog.web.dependencies import cookie_is_secure


def _request(*, scheme: str, cookie_secure: bool) -> SimpleNamespace:
    """Build a minimal request stub exposing app.state.settings and url.scheme."""
    settings = SimpleNamespace(cookie_secure=cookie_secure)
    state = SimpleNamespace(settings=settings)
    return SimpleNamespace(app=SimpleNamespace(state=state), url=SimpleNamespace(scheme=scheme))


@pytest.mark.parametrize(
    ("scheme", "cookie_secure", "expected"),
    [
        ("https", True, True),  # configured on, real HTTPS: mark Secure
        ("http", True, False),  # configured on, plain HTTP: drop Secure so the cookie works
        ("https", False, False),  # explicitly disabled: never mark Secure
        ("http", False, False),  # disabled and HTTP: never mark Secure
    ],
)
def test_cookie_is_secure(scheme: str, cookie_secure: bool, expected: bool) -> None:  # noqa: FBT001
    """Verify the Secure flag is honored only when the request arrived over HTTPS."""
    # Given a request over the scheme with the configured cookie_secure setting
    request = _request(scheme=scheme, cookie_secure=cookie_secure)

    # When resolving whether the cookie should be Secure / Then it matches the expectation
    assert cookie_is_secure(request) is expected  # ty: ignore[invalid-argument-type]
