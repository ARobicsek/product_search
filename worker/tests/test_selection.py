"""Diversity / anti-domination selection (Phase 32, REBUILD_PLAN §7)."""

from __future__ import annotations

from datetime import UTC, datetime

from product_search.models import Listing
from product_search.selection import select_for_display, vendor_key
from product_search.validators.price_sanity import FLAG_PRICE_ANOMALY_LOW


def _listing(price: float, seller: str, *, flags: list[str] | None = None) -> Listing:
    return Listing(
        source="serper_shopping",
        url="https://google.com/search?q=x",
        title="DJI Neo 2 Fly More Combo",
        fetched_at=datetime.now(tz=UTC),
        brand=None,
        mpn=None,
        attrs={},
        condition="new",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=price,
        kit_price_usd=None,
        quantity_available=None,
        seller_name=seller,
        seller_rating_pct=None,
        seller_feedback_count=None,
        ship_from_country=None,
        flags=list(flags or []),
    )


def test_cheapest_first_with_cap_and_truncation() -> None:
    listings = [
        _listing(120.0, "B&H"),
        _listing(100.0, "B&H"),
        _listing(110.0, "B&H"),
        _listing(105.0, "Walmart"),
        _listing(130.0, "Newegg"),
    ]
    result = select_for_display(listings, max_listings=3, per_vendor_cap=2)
    prices = [lst.price_usd for lst in result.displayed]
    assert prices == [100.0, 105.0, 110.0]  # cheapest-first, B&H capped at 2
    # The third B&H ($120) was held back by the cap and B&H is shown → overflow.
    assert result.overflow == {"b&h": 1}
    assert result.hidden_anomalies == 0


def test_price_anomaly_excluded_from_display() -> None:
    listings = [
        _listing(599.0, "B&H"),
        _listing(610.0, "Walmart"),
        _listing(67.2, "Scam", flags=[FLAG_PRICE_ANOMALY_LOW]),
    ]
    result = select_for_display(listings, max_listings=10, per_vendor_cap=3)
    assert result.hidden_anomalies == 1
    assert all(lst.price_usd != 67.2 for lst in result.displayed)
    # The anomaly never ranks #1.
    assert result.displayed[0].price_usd == 599.0


def test_unpriced_sorts_last() -> None:
    listings = [_listing(0.0, "A"), _listing(50.0, "B")]
    result = select_for_display(listings, max_listings=10, per_vendor_cap=3)
    assert result.displayed[0].price_usd == 50.0


def test_empty_input() -> None:
    result = select_for_display([], max_listings=10, per_vendor_cap=3)
    assert result.displayed == []
    assert result.overflow == {}


def test_vendor_key_normalises() -> None:
    assert vendor_key(_listing(1.0, "  B&H  ")) == "b&h"
    assert vendor_key(_listing(1.0, "")) == "(unknown vendor)"
