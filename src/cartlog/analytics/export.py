"""Render exported line-item rows to a downloadable CSV or JSON payload."""

from __future__ import annotations

import csv
import io
import json
from enum import StrEnum
from typing import TYPE_CHECKING

from cartlog.analytics.results import LineItemExportRow

if TYPE_CHECKING:
    from datetime import date

# CSV column order (also the header row) and the key order callers can rely on. Derived from
# the model so the columns cannot drift from LineItemExportRow's fields; model_fields preserves
# declaration order.
_FIELDS = list(LineItemExportRow.model_fields)


class ExportFormat(StrEnum):
    """Supported export serializations."""

    CSV = "csv"
    JSON = "json"


_MEDIA_TYPES = {ExportFormat.CSV: "text/csv", ExportFormat.JSON: "application/json"}


def render_export(rows: list[LineItemExportRow], fmt: ExportFormat) -> tuple[str, str, str]:
    """Render `rows` for `fmt`, returning (content, media_type, file_extension).

    `mode="json"` serialization keeps Decimals as strings (no float drift) and dates as ISO
    strings. An empty result still produces a valid file: a lone CSV header row, or `[]`.

    Args:
        rows: Line items to serialize.
        fmt: Target format (CSV or JSON).

    Returns:
        A tuple of (content string, MIME media type, file extension).
    """
    # mode="json" gives JSON-safe primitives (Decimal->str, date->ISO) for both formats.
    dicts = [row.model_dump(mode="json") for row in rows]

    if fmt is ExportFormat.JSON:
        return json.dumps(dicts, indent=2), _MEDIA_TYPES[fmt], fmt.value

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_FIELDS, extrasaction="ignore")
    writer.writeheader()
    # The csv module writes None as an empty string, so None-able fields need no remapping.
    writer.writerows(dicts)
    return buffer.getvalue(), _MEDIA_TYPES[fmt], fmt.value


def export_filename(fmt: ExportFormat, today: date) -> str:
    """Return a dated download filename, e.g. 'cartlog-export-2026-06-18.csv'.

    Args:
        fmt: The export format determining the file extension.
        today: The date to embed in the filename.

    Returns:
        A filename string suitable for use as a Content-Disposition attachment name.
    """
    return f"cartlog-export-{today.isoformat()}.{fmt.value}"
