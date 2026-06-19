"""Render exported line-item rows to a downloadable CSV or JSON payload."""

from __future__ import annotations

import csv
import io
import json
from enum import StrEnum
from typing import TYPE_CHECKING, get_args

from cartlog.analytics.results import LineItemExportRow

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from datetime import date

# CSV column order (also the header row) and the key order callers can rely on. Derived from
# the model so the columns cannot drift from LineItemExportRow's fields; model_fields preserves
# declaration order.
_FIELDS = list(LineItemExportRow.model_fields)

# Only string columns get spreadsheet-formula escaping; numeric and date columns must not, so a
# legitimate negative amount (e.g. a refund line_total) stays a number rather than text. Derived
# from the model's annotations: a field is text when `str` is one of its allowed types.
_TEXT_FIELDS = frozenset(
    name
    for name, info in LineItemExportRow.model_fields.items()
    if str in (get_args(info.annotation) or (info.annotation,))
)

# A spreadsheet evaluates a cell that begins with any of these as a formula (OWASP "CSV
# injection"). Receipt text reaches the export from OCR/LLM parsing, so a crafted description
# could otherwise execute on open.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


class ExportFormat(StrEnum):
    """Supported export serializations."""

    CSV = "csv"
    JSON = "json"


_MEDIA_TYPES = {ExportFormat.CSV: "text/csv", ExportFormat.JSON: "application/json"}


def media_type_for(fmt: ExportFormat) -> str:
    """Return the HTTP media type for an export format."""
    return _MEDIA_TYPES[fmt]


def _escape_csv_formula(value: str) -> str:
    """Prefix a single quote only when the cell actually starts with a formula trigger.

    Leaving every other cell untouched keeps the export a faithful copy of the data.
    """
    return f"'{value}" if value.startswith(_FORMULA_TRIGGERS) else value


def _csv_row(row: LineItemExportRow) -> dict[str, object]:
    """Serialize one row to JSON-safe cells, formula-escaping risky text cells only."""
    cells = row.model_dump(mode="json")
    return {
        key: _escape_csv_formula(value) if key in _TEXT_FIELDS and isinstance(value, str) else value
        for key, value in cells.items()
    }


def _drain(buffer: io.StringIO) -> str:
    """Return and clear the buffer's contents so the next row starts from empty."""
    chunk = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return chunk


def _iter_csv(rows: Iterable[LineItemExportRow]) -> Iterator[str]:
    """Yield the CSV header, then one chunk per row. None renders as an empty cell."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_FIELDS, extrasaction="ignore")
    writer.writeheader()
    yield _drain(buffer)
    for row in rows:
        writer.writerow(_csv_row(row))
        yield _drain(buffer)


def _iter_json(rows: Iterable[LineItemExportRow]) -> Iterator[str]:
    """Yield a JSON array one element at a time. An empty result yields `[]`."""
    yield "["
    for index, row in enumerate(rows):
        separator = "\n" if index == 0 else ",\n"
        yield separator + json.dumps(row.model_dump(mode="json"), indent=2)
    yield "\n]"


def iter_export(rows: Iterable[LineItemExportRow], fmt: ExportFormat) -> Iterator[str]:
    """Yield the export of `rows` as incremental text chunks for streaming.

    `mode="json"` serialization keeps Decimals as strings (no float drift) and dates as ISO
    strings. CSV text cells that begin with a spreadsheet formula trigger are quote-escaped;
    numeric columns are left intact. An empty result still yields a valid file (a lone CSV
    header row, or `[]`).

    Args:
        rows: Line items to serialize.
        fmt: Target format (CSV or JSON).

    Yields:
        Successive text chunks of the serialized payload.
    """
    if fmt is ExportFormat.JSON:
        yield from _iter_json(rows)
    else:
        yield from _iter_csv(rows)


def render_export(rows: list[LineItemExportRow], fmt: ExportFormat) -> tuple[str, str, str]:
    """Render `rows` for `fmt`, returning (content, media_type, file_extension).

    Builds the whole payload in memory; the web layer streams instead via `iter_export`. Use
    this where the full file is needed at once, such as writing it to disk from the CLI.

    Args:
        rows: Line items to serialize.
        fmt: Target format (CSV or JSON).

    Returns:
        A tuple of (content string, MIME media type, file extension).
    """
    content = "".join(iter_export(rows, fmt))
    return content, _MEDIA_TYPES[fmt], fmt.value


def export_filename(fmt: ExportFormat, today: date) -> str:
    """Return a dated download filename, e.g. 'cartlog-export-2026-06-18.csv'.

    Args:
        fmt: The export format determining the file extension.
        today: The date to embed in the filename.

    Returns:
        A filename string suitable for use as a Content-Disposition attachment name.
    """
    return f"cartlog-export-{today.isoformat()}.{fmt.value}"
