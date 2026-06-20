"""Shared Jinja2 templates instance for the web layer.

Kept in its own module so routers and the app factory can both import `templates`
without creating an import cycle through `app.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi.templating import Jinja2Templates

from cartlog.analytics.ranges import range_label
from cartlog.web.units_display import format_normalized
from cartlog.web.viz import bar_percents, heatmap_intensity, sparkline_points

if TYPE_CHECKING:
    from starlette.requests import Request

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _auth_context(request: Request) -> dict[str, Any]:
    """Return per-request auth fields injected into every template context.

    Opens a short-lived DB session from the app's session factory to resolve the
    current user. Uses getattr with a default for csrf_token so paths where the
    CSRF middleware did not run (e.g. /static) never KeyError.

    Args:
        request: The current Starlette request.

    Returns:
        dict containing current_user (User or None), csrf_token (str), can_edit (bool),
        and is_admin (bool).
    """
    # Import here to avoid a circular import: auth.py imports dependencies.py which
    # does not import templating.py, so this direction is safe at call time.
    from cartlog.db.models import Role  # noqa: PLC0415
    from cartlog.web.auth import load_user, role_satisfies  # noqa: PLC0415

    factory = request.app.state.session_factory
    with factory() as session:
        current_user = load_user(request, session)

    # Pre-compute role booleans so templates can gate UI elements without
    # repeating the role comparison logic in every template.
    can_edit: bool = current_user is not None and role_satisfies(current_user.role, Role.EDITOR)
    is_admin: bool = current_user is not None and role_satisfies(current_user.role, Role.ADMIN)

    return {
        "current_user": current_user,
        "can_edit": can_edit,
        "is_admin": is_admin,
        # csrf_token is set by CsrfMiddleware before the route runs; the default avoids
        # a KeyError on paths that bypass the middleware (e.g. lifespan health probes).
        "csrf_token": getattr(request.state, "csrf_token", ""),
        # today is injected so templates never call now() or risk an em-dash fallback.
        "today": datetime.now(UTC).date().isoformat(),
    }


templates = Jinja2Templates(directory=str(_TEMPLATES_DIR), context_processors=[_auth_context])

# Expose the SVG-coordinate helpers to every template so the viz macros can call them.
# ty cannot resolve Jinja2's env.globals union type against arbitrary callables; the
# cast widens the dict to Any so assignments are accepted without ignoring all errors.
_globals: dict[str, Any] = cast("dict[str, Any]", templates.env.globals)
_globals["sparkline_points"] = sparkline_points
_globals["bar_percents"] = bar_percents
_globals["heatmap_intensity"] = heatmap_intensity
# Single source of truth for range captions, shared with the chips and the provenance line.
_globals["range_label"] = range_label

_filters: dict[str, Any] = cast("dict[str, Any]", templates.env.filters)
# Templates call `value | normalized_price(dimension, status, unit_system)`.
_filters["normalized_price"] = format_normalized
