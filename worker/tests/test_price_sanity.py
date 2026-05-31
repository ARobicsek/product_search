"""Price-sanity + ship-from gate (Phase 32, ADR-131 P1)."""

from __future__ import annotations

from datetime import UTC, datetime

from product_search.models import Listing
from product_search.validators.price_sanity import (
    FLAG_PRICE_ANOMALY_HIGH,
    FLAG_PRICE_ANOMALY_LOW,
    annotate_price_anomalies,
    apply_ship_from_gate,
)


def _listing(price: float, *, ship_from: str | None = None) -> Listing:
    return Listing(
        source="serper_shopping",
        url="https://google.com/search?q=x",
        title="DJI Neo 2 Fly More Combo",
        fetched_at=datetime.now(tz=UTC),
        brand=None,
        mpn=None,
        attrs={},
        condition="",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=price,
        kit_price_usd=None,
        quantity_available=None,
        seller_name="B&H",
        seller_rating_pct=None,
        seller_feedback_count=None,
        ship_from_country=ship_from,
    )


def test_low_anomaly_flagged() -> None:
    listings = [_listing(p) for p in (599.0, 610.0, 605.0, 620.0, 67.2)]
    low = annotate_price_anomalies(listings)
    assert low == 1
    assert FLAG_PRICE_ANOMALY_LOW in listings[-1].flags  # the $67.20 outlier
    assert all(FLAG_PRICE_ANOMALY_LOW not in lst.flags for lst in listings[:-1])


def test_high_anomaly_flagged_not_counted_as_low() -> None:
    listings = [_listing(p) for p in (100.0, 105.0, 110.0, 102.0, 9999.0)]
    low = annotate_price_anomalies(listings)
    assert low == 0
    assert FLAG_PRICE_ANOMALY_HIGH in listings[-1].flags


def test_modest_discount_not_flagged() -> None:
    # A 20%-cheaper genuine deal must survive — only egregious outliers go.
    listings = [_listing(p) for p in (600.0, 610.0, 590.0, 480.0)]
    annotate_price_anomalies(listings)
    assert all(FLAG_PRICE_ANOMALY_LOW not in lst.flags for lst in listings)


def test_too_few_samples_is_noop() -> None:
    listings = [_listing(p) for p in (600.0, 50.0)]
    assert annotate_price_anomalies(listings) == 0
    assert all(not lst.flags for lst in listings)


def test_unpriced_never_flagged() -> None:
    listings = [_listing(p) for p in (599.0, 610.0, 605.0, 620.0)]
    listings.append(_listing(0.0))  # unknown price
    annotate_price_anomalies(listings)
    assert not listings[-1].flags


def test_ship_from_gate_flags_foreign() -> None:
    listings = [_listing(599.0, ship_from="US"), _listing(120.0, ship_from="CN")]
    kept, flagged = apply_ship_from_gate(listings, ["US"])
    assert flagged == 1
    assert len(kept) == 2  # flag-only by default
    assert "ship_from_cn" in listings[1].flags
    assert not listings[0].flags


def test_ship_from_gate_can_drop() -> None:
    listings = [_listing(599.0, ship_from="US"), _listing(120.0, ship_from="CN")]
    kept, flagged = apply_ship_from_gate(listings, ["US"], drop=True)
    assert flagged == 1
    assert [lst.ship_from_country for lst in kept] == ["US"]


def test_ship_from_unknown_is_kept_unflagged() -> None:
    listings = [_listing(599.0, ship_from=None)]
    kept, flagged = apply_ship_from_gate(listings, ["US"])
    assert flagged == 0
    assert not listings[0].flags


def test_ship_from_gate_disabled_when_no_allowlist() -> None:
    listings = [_listing(599.0, ship_from="CN")]
    kept, flagged = apply_ship_from_gate(listings, None)
    assert flagged == 0
    assert kept == listings
