"""Preferences routes: user-scoped display toggles stored in cookies."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter()


@router.post("/preferences/unit-system")
def toggle_unit_system(request: Request) -> Response:
    """Flip the unit_system cookie and ask htmx to refresh the current page.

    The cookie stores a presentation-only preference (no PII), so a 1-year
    max_age is acceptable without a server-side session.
    """
    current = "metric" if request.cookies.get("unit_system") == "metric" else "imperial"
    new_value = "imperial" if current == "metric" else "metric"
    response = Response(status_code=204)
    # 1 year; presentation-only preference, no PII.
    response.set_cookie("unit_system", new_value, max_age=31_536_000, samesite="lax")
    response.headers["HX-Refresh"] = "true"
    return response
