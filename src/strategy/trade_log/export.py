"""Export trade-log rows to CSV / JSON for offline analysis."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable

# Column order for CSV / JSON export (excludes created_at used internally).
FIELDS = [
    "id", "ts", "venue", "symbol", "tag", "side", "qty", "price",
    "notional_usd", "fee_usd", "pnl_usd", "strategy", "reason", "note",
]


def to_csv(rows: Iterable[dict]) -> str:
    """Render rows as CSV. Unknown keys are ignored; missing keys are empty."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def to_json(rows: Iterable[dict]) -> str:
    """Render rows as pretty JSON (UTF-8, so Chinese tags/notes survive)."""
    return json.dumps(list(rows), ensure_ascii=False, indent=2)
