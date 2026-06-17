"""Shared sort direction and a helper for applying it to a query.

Lives in the `db` package so both the web routers and the analytics service can apply a sort
direction without a web<->analytics import cycle.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import SQLColumnExpression
    from sqlalchemy.orm import Query


class SortDir(StrEnum):
    """Sort direction for the sortable tables."""

    ASC = "asc"
    DESC = "desc"


def apply_sort(query: Query, column: SQLColumnExpression, direction: SortDir) -> Query:
    """Order `query` by `column` in the given direction.

    Use everywhere a column-plus-direction pair is turned into an ORDER BY so the asc/desc
    branch is written once. `column` is any orderable SQL expression (a mapped column or a
    `func.*` expression).
    """
    return query.order_by(column.desc() if direction == SortDir.DESC else column.asc())
