"""Convert the stored metric-base normalized price into a display string.

Storage is always metric base ($/g, $/ml, $/each). Weight/volume use 3 decimals because
per-oz figures on cheap bulk goods round to zero at 2; count and per-100 use 2 decimals.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from cartlog.constants import COUNT, VOLUME, WEIGHT
from cartlog.units import RESOLVED

if TYPE_CHECKING:
    from fastapi import Request

_OZ_PER_G = Decimal("28.3495")
_FLOZ_PER_ML = Decimal("29.5735")
_HUNDRED = Decimal(100)


def format_normalized(
    normalized_unit_price: Decimal | None,
    dimension: str | None,
    status: str,
    system: str,
) -> str:
    """Render a normalized price for display, or 'n/a' when the line is not resolved.

    Args:
        normalized_unit_price: The stored $/base-unit price ($/g, $/ml, or $/each).
        dimension: The measure dimension: "weight", "volume", or "count".
        status: The normalization status; only "resolved" lines produce a price.
        system: Display unit system: "imperial" or "metric".

    Returns:
        A formatted price string like "$0.109/oz" or "n/a" for unresolved rows.
    """
    if status != RESOLVED or normalized_unit_price is None or dimension is None:
        return "n/a"
    if dimension == COUNT:
        return f"${normalized_unit_price:.2f}/ea"
    if system == "metric":
        per_100 = normalized_unit_price * _HUNDRED
        suffix = "100g" if dimension == WEIGHT else "100ml"
        return f"${per_100:.2f}/{suffix}"
    if dimension == WEIGHT:
        return f"${normalized_unit_price * _OZ_PER_G:.3f}/oz"
    if dimension == VOLUME:
        return f"${normalized_unit_price * _FLOZ_PER_ML:.3f}/fl oz"
    return "n/a"


def read_unit_system(request: Request) -> str:
    """Return the caller's preferred unit system from the cookie; imperial is the default.

    Args:
        request: The incoming FastAPI request carrying cookies.

    Returns:
        "metric" when the cookie is explicitly set to metric, otherwise "imperial".
    """
    return "metric" if request.cookies.get("unit_system") == "metric" else "imperial"
