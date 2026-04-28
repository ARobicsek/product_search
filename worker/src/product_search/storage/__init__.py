"""Storage layer: SQLite persistence + CSV dumps + pure-Python diff.

The store is the canonical record of every listing observed across runs,
keyed by ``(url, fetched_at)``. CSVs in ``worker/data/<slug>/<date>.csv``
are a flat, gitignored mirror for inspection. The diff engine consumes
two snapshots (lists of ``Listing``) and reports new / dropped / changed.
"""

from product_search.storage.db import (
    connect,
    insert_listings,
    query_snapshot_for_date,
    snapshot_dates,
)
from product_search.storage.diff import DiffResult, PriceChange, diff_snapshots

__all__ = [
    "DiffResult",
    "PriceChange",
    "connect",
    "diff_snapshots",
    "insert_listings",
    "query_snapshot_for_date",
    "snapshot_dates",
]
