"""SQLite persistence for ``Listing`` rows.

The store keeps one row per ``(url, fetched_at)`` so that re-running on the
same day adds rows instead of overwriting them. ``attrs`` and ``flags`` are
JSON-encoded text columns; everything else maps to a native SQLite column.

The default DB path is ``worker/data/<slug>/listings.sqlite`` (gitignored).
Tests pass ``db_path=":memory:"`` for in-process snapshots.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_search.models import Listing

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    url                   TEXT NOT NULL,
    fetched_at            TEXT NOT NULL,
    source                TEXT NOT NULL,
    title                 TEXT NOT NULL,
    brand                 TEXT,
    mpn                   TEXT,
    attrs_json            TEXT NOT NULL,
    condition             TEXT NOT NULL,
    is_kit                INTEGER NOT NULL,
    kit_module_count      INTEGER NOT NULL,
    unit_price_usd        REAL NOT NULL,
    kit_price_usd         REAL,
    quantity_available    INTEGER,
    seller_name           TEXT NOT NULL,
    seller_rating_pct     REAL,
    seller_feedback_count INTEGER,
    ship_from_country     TEXT,
    qvl_status            TEXT,
    flags_json            TEXT NOT NULL,
    total_for_target_usd  REAL,
    PRIMARY KEY (url, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_listings_fetched_at ON listings(fetched_at);
"""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Walk up from this file until we find the directory containing ``products/``."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "products").is_dir():
            return parent
    # Fallback: cwd. The tests pass an explicit db_path, so this is just a
    # courtesy for live runs.
    return Path.cwd()


def default_db_path(slug: str) -> Path:
    """Return ``worker/data/<slug>/listings.sqlite`` under the repo root."""
    return _repo_root() / "worker" / "data" / slug / "listings.sqlite"


# ---------------------------------------------------------------------------
# Connection / schema
# ---------------------------------------------------------------------------


def connect(slug: str, *, db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the listings DB for *slug*.

    Ensures the schema exists on every call. Caller is responsible for
    ``conn.close()``.
    """
    if db_path is None:
        path = default_db_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
    else:
        # ":memory:" or any explicit path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))

    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Insert / query
# ---------------------------------------------------------------------------


def insert_listings(conn: sqlite3.Connection, listings: Iterable[Listing]) -> int:
    """Insert listings. ``INSERT OR REPLACE`` so re-running the same instant
    does not raise on the composite PK.

    Returns the number of rows written.
    """
    rows = [_listing_to_row(lst) for lst in listings]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO listings (
            url, fetched_at, source, title, brand, mpn, attrs_json,
            condition, is_kit, kit_module_count, unit_price_usd,
            kit_price_usd, quantity_available, seller_name,
            seller_rating_pct, seller_feedback_count, ship_from_country,
            qvl_status, flags_json, total_for_target_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def snapshot_dates(conn: sqlite3.Connection) -> list[str]:
    """Return distinct ``fetched_at`` dates (YYYY-MM-DD), descending."""
    cur = conn.execute(
        "SELECT DISTINCT substr(fetched_at, 1, 10) AS d "
        "FROM listings ORDER BY d DESC"
    )
    return [str(r["d"]) for r in cur.fetchall()]


def query_snapshot_for_date(conn: sqlite3.Connection, date_str: str) -> list[Listing]:
    """Return the latest row per URL for the given YYYY-MM-DD date.

    If a URL was fetched multiple times on the same date (e.g. two ad-hoc
    runs), keep the most recent ``fetched_at`` only — the snapshot is what
    the user "saw last" for that day.
    """
    cur = conn.execute(
        """
        SELECT * FROM listings
        WHERE substr(fetched_at, 1, 10) = ?
        AND fetched_at = (
            SELECT MAX(fetched_at) FROM listings AS inner_l
            WHERE inner_l.url = listings.url
              AND substr(inner_l.fetched_at, 1, 10) = ?
        )
        ORDER BY url
        """,
        (date_str, date_str),
    )
    return [_row_to_listing(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Row <-> Listing
# ---------------------------------------------------------------------------


def _listing_to_row(lst: Listing) -> tuple[Any, ...]:
    return (
        lst.url,
        lst.fetched_at.isoformat(),
        lst.source,
        lst.title,
        lst.brand,
        lst.mpn,
        json.dumps(lst.attrs, sort_keys=True),
        lst.condition,
        1 if lst.is_kit else 0,
        lst.kit_module_count,
        lst.unit_price_usd,
        lst.kit_price_usd,
        lst.quantity_available,
        lst.seller_name,
        lst.seller_rating_pct,
        lst.seller_feedback_count,
        lst.ship_from_country,
        lst.qvl_status,
        json.dumps(lst.flags),
        lst.total_for_target_usd,
    )


def _row_to_listing(row: sqlite3.Row) -> Listing:
    fetched_at_raw = row["fetched_at"]
    # Python 3.11+ datetime.fromisoformat handles full ISO 8601 incl. "+00:00".
    fetched_at = datetime.fromisoformat(fetched_at_raw)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)

    return Listing(
        source=row["source"],
        url=row["url"],
        title=row["title"],
        fetched_at=fetched_at,
        brand=row["brand"],
        mpn=row["mpn"],
        attrs=json.loads(row["attrs_json"]),
        condition=row["condition"],
        is_kit=bool(row["is_kit"]),
        kit_module_count=row["kit_module_count"],
        unit_price_usd=row["unit_price_usd"],
        kit_price_usd=row["kit_price_usd"],
        quantity_available=row["quantity_available"],
        seller_name=row["seller_name"],
        seller_rating_pct=row["seller_rating_pct"],
        seller_feedback_count=row["seller_feedback_count"],
        ship_from_country=row["ship_from_country"],
        qvl_status=row["qvl_status"],
        flags=json.loads(row["flags_json"]),
        total_for_target_usd=row["total_for_target_usd"],
    )
