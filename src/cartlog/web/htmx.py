"""Small helpers for the server side of the htmx interaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request


def wants_partial(request: Request) -> bool:
    """Return True when htmx issued the request and expects an HTML fragment.

    A history-cache miss re-fetches the URL with HX-Request set but swaps the response into the
    whole page, so it needs the full document, not a fragment. Exclude it explicitly.
    """
    if request.headers.get("HX-History-Restore-Request") == "true":
        return False
    return request.headers.get("HX-Request") == "true"
