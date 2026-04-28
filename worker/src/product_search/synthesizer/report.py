"""Helpers for writing the daily report markdown.

Reports are committed to ``reports/<slug>/<date>.md`` at the repo root.
The synthesizer module owns producing the markdown; this module owns
where to put it.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path

from product_search.storage.db import _repo_root


def default_report_path(slug: str, snapshot_date: _date) -> Path:
    """Return ``reports/<slug>/<YYYY-MM-DD>.md`` under the repo root."""
    return _repo_root() / "reports" / slug / f"{snapshot_date.isoformat()}.md"


def write_report(path: Path, body_md: str) -> Path:
    """Write *body_md* to *path*, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = body_md.rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    return path
