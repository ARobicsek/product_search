"""Per-run CSV dump of listings to ``reports/<slug>/data/<run-timestamp>.csv``.

One CSV per worker run (not per day) so the full raw listing set for every
run is preserved instead of being overwritten by the next run on the same
date. Lives under ``reports/`` rather than ``worker/data/`` because that
tree is committed back from GitHub Actions; the previous ``worker/data/``
location is gitignored and ephemeral on the runner.

``attrs`` and ``flags`` are JSON strings so the round-trip is lossless.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from product_search.models import Listing
from product_search.storage.db import _repo_root

CSV_FIELDS: tuple[str, ...] = (
    "source",
    "url",
    "title",
    "fetched_at",
    "brand",
    "mpn",
    "attrs",
    "condition",
    "is_kit",
    "kit_module_count",
    "unit_price_usd",
    "kit_price_usd",
    "quantity_available",
    "seller_name",
    "seller_rating_pct",
    "seller_feedback_count",
    "ship_from_country",
    "qvl_status",
    "flags",
    "total_for_target_usd",
)


def default_csv_path(slug: str, fetched_at: datetime) -> Path:
    """Return ``reports/<slug>/data/<YYYY-MM-DDTHH-MM-SSZ>.csv``.

    One CSV per run, keyed on the run's ``fetched_at`` UTC timestamp. The
    filename uses ``-`` instead of ``:`` so it's a valid filename on Windows
    too (NTFS reserves ``:`` for ADS). Always emitted in UTC regardless of
    the caller's local timezone, so prod (GHA, UTC) and dev (local TZ) sort
    consistently and don't double-up on DST boundaries.
    """
    from datetime import UTC

    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    stamp = fetched_at.astimezone(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    return _repo_root() / "reports" / slug / "data" / f"{stamp}.csv"


def write_snapshot_csv(
    path: Path, listings: Iterable[Listing]
) -> int:
    """Write listings to ``path``. Returns the number of rows written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(listings)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for lst in rows:
            writer.writerow(_listing_to_csv_row(lst))
    return len(rows)


def read_snapshot_csv(path: Path) -> list[Listing]:
    """Read listings back from a CSV produced by ``write_snapshot_csv``."""
    listings: list[Listing] = []
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            listings.append(_csv_row_to_listing(row))
    return listings


# ---------------------------------------------------------------------------
# Row <-> Listing
# ---------------------------------------------------------------------------


def _listing_to_csv_row(lst: Listing) -> dict[str, str]:
    return {
        "source": lst.source,
        "url": lst.url,
        "title": lst.title,
        "fetched_at": lst.fetched_at.isoformat(),
        "brand": lst.brand or "",
        "mpn": lst.mpn or "",
        "attrs": json.dumps(lst.attrs, sort_keys=True),
        "condition": lst.condition,
        "is_kit": "1" if lst.is_kit else "0",
        "kit_module_count": str(lst.kit_module_count),
        "unit_price_usd": f"{lst.unit_price_usd}",
        "kit_price_usd": "" if lst.kit_price_usd is None else f"{lst.kit_price_usd}",
        "quantity_available": (
            "" if lst.quantity_available is None else str(lst.quantity_available)
        ),
        "seller_name": lst.seller_name,
        "seller_rating_pct": (
            "" if lst.seller_rating_pct is None else f"{lst.seller_rating_pct}"
        ),
        "seller_feedback_count": (
            "" if lst.seller_feedback_count is None else str(lst.seller_feedback_count)
        ),
        "ship_from_country": lst.ship_from_country or "",
        "qvl_status": lst.qvl_status or "",
        "flags": json.dumps(lst.flags),
        "total_for_target_usd": (
            "" if lst.total_for_target_usd is None else f"{lst.total_for_target_usd}"
        ),
    }


def _csv_row_to_listing(row: dict[str, str]) -> Listing:
    return Listing(
        source=row["source"],
        url=row["url"],
        title=row["title"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        brand=row["brand"] or None,
        mpn=row["mpn"] or None,
        attrs=json.loads(row["attrs"]),
        condition=row["condition"],
        is_kit=row["is_kit"] == "1",
        kit_module_count=int(row["kit_module_count"]),
        unit_price_usd=float(row["unit_price_usd"]),
        kit_price_usd=float(row["kit_price_usd"]) if row["kit_price_usd"] else None,
        quantity_available=(
            int(row["quantity_available"]) if row["quantity_available"] else None
        ),
        seller_name=row["seller_name"],
        seller_rating_pct=(
            float(row["seller_rating_pct"]) if row["seller_rating_pct"] else None
        ),
        seller_feedback_count=(
            int(row["seller_feedback_count"]) if row["seller_feedback_count"] else None
        ),
        ship_from_country=row["ship_from_country"] or None,
        qvl_status=row["qvl_status"] or None,
        flags=json.loads(row["flags"]),
        total_for_target_usd=(
            float(row["total_for_target_usd"]) if row["total_for_target_usd"] else None
        ),
    )
