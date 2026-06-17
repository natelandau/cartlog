"""Small reusable helpers for building SQL queries safely."""

from __future__ import annotations

from functools import reduce
from operator import or_
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import SQLColumnExpression


def escape_like(text: str) -> str:
    r"""Escape LIKE wildcards so user text matches literally (grocery names often contain %).

    Use with ``escape="\\"`` on the ``like``/``ilike`` call so the backslash escapes are honored.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def text_filter(term: str, *columns: SQLColumnExpression) -> SQLColumnExpression[bool]:
    r"""Build a case-insensitive "term appears in any of these columns" filter.

    Escapes LIKE wildcards in `term` (see `escape_like`) and ORs a `column ILIKE %term%` clause
    across every column, so a multi-column free-text search is expressed once. Pass the mapped
    columns to search; the result is ready to hand to ``query.filter(...)``.
    """
    like = f"%{escape_like(term)}%"
    return reduce(or_, (column.ilike(like, escape="\\") for column in columns))
