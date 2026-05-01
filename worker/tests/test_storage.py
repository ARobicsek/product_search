"""Tests for the Phase 4 storage layer (SQLite + CSV) and diff engine.

Coverage:
  SQLite store (in-memory)
    1. Schema is created on connect.
    2. insert_listings round-trips a single Listing.
    3. Composite PK (url, fetched_at) lets the same URL co-exist on two days.
    4. query_snapshot_for_date returns latest-per-URL within a day.
    5. snapshot_dates is descending and de-duplicated.

  CSV dump
    6. write_snapshot_csv + read_snapshot_csv preserves every Listing field.

  Diff engine (pure-Python)
    7. New URL only in current -> reported as 'new'.
    8. URL only in previous -> reported as 'dropped'.
    9. Price move >=5% -> reported as 'changed' with correct pct_change.
   10. Price move <5% -> not reported.
   11. is_material is False on empty diff, True on any non-empty bucket.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from product_search.models import Listing
from product_search.storage import (
    DiffResult,
    diff_snapshots,
    insert_listings,
    query_snapshot_for_date,
    snapshot_dates,
)
from product_search.storage.csv_dump import (
    default_csv_path,
    read_snapshot_csv,
    write_snapshot_csv,
)
from product_search.storage.db import connect

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _listing(
    url: str,
    *,
    fetched_at: datetime | None = None,
    unit_price_usd: float = 100.0,
    title: str = "Test Listing",
    flags: list[str] | None = None,
    qvl_status: str | None = None,
    total_for_target_usd: float | None = None,
) -> Listing:
    return Listing(
        source="ebay_search",
        url=url,
        title=title,
        fetched_at=fetched_at or datetime.now(tz=UTC),
        brand="Samsung",
        mpn="M321R4GA0BB0-CQK",
        attrs={"capacity_gb": 32, "speed_mts": 4800, "form_factor": "RDIMM"},
        condition="new",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=unit_price_usd,
        kit_price_usd=None,
        quantity_available=10,
        seller_name="some_seller",
        seller_rating_pct=99.5,
        seller_feedback_count=5000,
        ship_from_country="US",
        qvl_status=qvl_status,
        flags=flags if flags is not None else [],
        total_for_target_usd=total_for_target_usd,
    )


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


def test_db_connect_creates_schema() -> None:
    conn = connect("test-slug", db_path=":memory:")
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
        )
        assert cur.fetchone() is not None
    finally:
        conn.close()


def test_insert_round_trips_a_single_listing() -> None:
    conn = connect("test-slug", db_path=":memory:")
    try:
        when = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
        original = _listing(
            "https://www.ebay.com/itm/1",
            fetched_at=when,
            flags=["china_shipping"],
            qvl_status="qvl",
            total_for_target_usd=800.0,
        )
        n = insert_listings(conn, [original])
        assert n == 1

        rows = query_snapshot_for_date(conn, "2026-04-28")
        assert len(rows) == 1
        got = rows[0]

        # Spot-check every field, including JSON-encoded ones.
        assert got.url == original.url
        assert got.fetched_at == original.fetched_at
        assert got.title == original.title
        assert got.brand == original.brand
        assert got.mpn == original.mpn
        assert got.attrs == original.attrs
        assert got.condition == original.condition
        assert got.is_kit == original.is_kit
        assert got.kit_module_count == original.kit_module_count
        assert got.unit_price_usd == original.unit_price_usd
        assert got.kit_price_usd == original.kit_price_usd
        assert got.quantity_available == original.quantity_available
        assert got.seller_name == original.seller_name
        assert got.seller_rating_pct == original.seller_rating_pct
        assert got.seller_feedback_count == original.seller_feedback_count
        assert got.ship_from_country == original.ship_from_country
        assert got.qvl_status == original.qvl_status
        assert got.flags == original.flags
        assert got.total_for_target_usd == original.total_for_target_usd
    finally:
        conn.close()


def test_same_url_two_days_both_persisted() -> None:
    conn = connect("test-slug", db_path=":memory:")
    try:
        url = "https://www.ebay.com/itm/multi"
        day1 = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
        day2 = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)

        insert_listings(
            conn,
            [
                _listing(url, fetched_at=day1, unit_price_usd=100.0),
                _listing(url, fetched_at=day2, unit_price_usd=110.0),
            ],
        )

        d1 = query_snapshot_for_date(conn, "2026-04-27")
        d2 = query_snapshot_for_date(conn, "2026-04-28")
        assert len(d1) == 1
        assert len(d2) == 1
        assert d1[0].unit_price_usd == 100.0
        assert d2[0].unit_price_usd == 110.0
    finally:
        conn.close()


def test_query_snapshot_keeps_latest_per_url_within_day() -> None:
    """If the same URL is fetched twice on the same day, the latest wins."""
    conn = connect("test-slug", db_path=":memory:")
    try:
        url = "https://www.ebay.com/itm/dup"
        morning = datetime(2026, 4, 28, 6, 0, tzinfo=UTC)
        evening = datetime(2026, 4, 28, 18, 0, tzinfo=UTC)

        insert_listings(
            conn,
            [
                _listing(url, fetched_at=morning, unit_price_usd=100.0),
                _listing(url, fetched_at=evening, unit_price_usd=120.0),
            ],
        )

        rows = query_snapshot_for_date(conn, "2026-04-28")
        assert len(rows) == 1
        assert rows[0].unit_price_usd == 120.0
    finally:
        conn.close()


def test_snapshot_dates_descending_and_unique() -> None:
    conn = connect("test-slug", db_path=":memory:")
    try:
        base = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
        insert_listings(
            conn,
            [
                _listing("u1", fetched_at=base),
                _listing(
                    "u2",
                    fetched_at=base + timedelta(hours=1),  # same date
                ),
                _listing(
                    "u3",
                    fetched_at=base - timedelta(days=2),  # 2026-04-26
                ),
                _listing(
                    "u4",
                    fetched_at=base - timedelta(days=1),  # 2026-04-27
                ),
            ],
        )

        dates = snapshot_dates(conn)
        assert dates == ["2026-04-28", "2026-04-27", "2026-04-26"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CSV dump
# ---------------------------------------------------------------------------


def test_csv_round_trip_preserves_listing(tmp_path: Path) -> None:
    when = datetime(2026, 4, 28, 14, 30, 5, tzinfo=UTC)
    original = _listing(
        "https://www.ebay.com/itm/csv",
        fetched_at=when,
        flags=["china_shipping", "low_seller_feedback"],
        qvl_status="inferred-compatible",
        total_for_target_usd=950.5,
    )
    # Add an attr value that exercises JSON encoding (non-string).
    original.attrs["extra_int"] = 42

    csv_path = tmp_path / "snapshot.csv"
    written = write_snapshot_csv(csv_path, [original])
    assert written == 1
    assert csv_path.is_file()

    [restored] = read_snapshot_csv(csv_path)
    assert restored.url == original.url
    assert restored.fetched_at == original.fetched_at
    assert restored.attrs == original.attrs
    assert restored.flags == original.flags
    assert restored.qvl_status == original.qvl_status
    assert restored.total_for_target_usd == original.total_for_target_usd
    assert restored.unit_price_usd == original.unit_price_usd
    # None-valued optionals should round-trip as None, not ''.
    assert restored.kit_price_usd is None


def test_default_csv_path_is_per_run_under_reports() -> None:
    # The CSV must live under reports/<slug>/data/ (committable tree, not
    # gitignored worker/data/) and must include a UTC timestamp so multiple
    # runs on the same date don't overwrite each other.
    when = datetime(2026, 4, 30, 22, 30, 15, tzinfo=UTC)
    p = default_csv_path("test-slug", when)

    assert p.name == "2026-04-30T22-30-15Z.csv"
    assert p.parent.name == "data"
    assert p.parent.parent.name == "test-slug"
    assert p.parent.parent.parent.name == "reports"


def test_default_csv_path_emits_utc_regardless_of_input_tz() -> None:
    # Same instant, two different input zones — must produce the same path
    # so dev (local TZ) and prod (UTC GHA runner) sort identically.
    from datetime import timedelta, timezone

    instant_utc = datetime(2026, 4, 30, 22, 30, 15, tzinfo=UTC)
    instant_pst = instant_utc.astimezone(timezone(timedelta(hours=-8)))

    assert default_csv_path("s", instant_utc) == default_csv_path("s", instant_pst)


def test_default_csv_path_treats_naive_datetime_as_utc() -> None:
    naive = datetime(2026, 4, 30, 22, 30, 15)  # no tzinfo
    aware = datetime(2026, 4, 30, 22, 30, 15, tzinfo=UTC)
    assert default_csv_path("s", naive) == default_csv_path("s", aware)


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------


def test_diff_reports_new_listings() -> None:
    prev = [_listing("https://a", unit_price_usd=100.0)]
    curr = [
        _listing("https://a", unit_price_usd=100.0),
        _listing("https://b", unit_price_usd=200.0),
    ]
    result = diff_snapshots(prev, curr)
    assert [lst.url for lst in result.new] == ["https://b"]
    assert result.dropped == []
    assert result.changed == []
    assert result.is_material is True


def test_diff_reports_dropped_listings() -> None:
    prev = [
        _listing("https://a", unit_price_usd=100.0),
        _listing("https://b", unit_price_usd=200.0),
    ]
    curr = [_listing("https://a", unit_price_usd=100.0)]
    result = diff_snapshots(prev, curr)
    assert result.new == []
    assert [lst.url for lst in result.dropped] == ["https://b"]
    assert result.changed == []


def test_diff_reports_price_change_above_threshold() -> None:
    prev = [_listing("https://a", unit_price_usd=100.0)]
    curr = [_listing("https://a", unit_price_usd=110.0)]  # +10%
    result = diff_snapshots(prev, curr)
    assert result.new == []
    assert result.dropped == []
    assert len(result.changed) == 1
    ch = result.changed[0]
    assert ch.url == "https://a"
    assert ch.old_price_usd == 100.0
    assert ch.new_price_usd == 110.0
    assert abs(ch.pct_change - 0.10) < 1e-9
    assert ch.direction == "up"


def test_diff_ignores_price_change_below_threshold() -> None:
    prev = [_listing("https://a", unit_price_usd=100.0)]
    curr = [_listing("https://a", unit_price_usd=104.0)]  # +4%, under 5%
    result = diff_snapshots(prev, curr)
    assert result.changed == []
    assert result.is_material is False


def test_diff_threshold_is_inclusive() -> None:
    """A move of exactly 5% counts as changed."""
    prev = [_listing("https://a", unit_price_usd=100.0)]
    curr = [_listing("https://a", unit_price_usd=95.0)]  # -5% exactly
    result = diff_snapshots(prev, curr)
    assert len(result.changed) == 1
    assert result.changed[0].direction == "down"


def test_diff_combined_scenario() -> None:
    prev = [
        _listing("https://stable", unit_price_usd=300.0),
        _listing("https://drop", unit_price_usd=400.0),
        _listing("https://moved", unit_price_usd=200.0),
    ]
    curr = [
        _listing("https://stable", unit_price_usd=302.0),     # <5%, ignored
        _listing("https://moved", unit_price_usd=180.0),      # -10%, changed
        _listing("https://newone", unit_price_usd=500.0),     # new
    ]
    result = diff_snapshots(prev, curr)
    assert [lst.url for lst in result.new] == ["https://newone"]
    assert [lst.url for lst in result.dropped] == ["https://drop"]
    assert [ch.url for ch in result.changed] == ["https://moved"]
    assert result.is_material is True


def test_empty_diff_is_not_material() -> None:
    result = DiffResult()
    assert result.is_material is False
