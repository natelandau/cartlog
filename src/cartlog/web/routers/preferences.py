"""Preferences routes: user-scoped display toggles stored in cookies."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from cartlog.web.guards import require_read

# Preferences are user-scoped, so require at least read access (no anonymous preference changes).
router = APIRouter(dependencies=[Depends(require_read)])


@router.post("/preferences/unit-system")
# require_read (not require_editor) is intentional: this route writes only a client-side
# display cookie, not server state, so a viewer or anonymous visitor may set their own
# unit preference without needing editor privileges.
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
