"""Small helpers for the server side of the htmx interaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


def wants_partial(request: Request) -> bool:
    """Return True when htmx issued the request and expects an HTML fragment."""
    return request.headers.get("HX-Request") == "true"
