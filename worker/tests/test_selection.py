"""Diversity / anti-domination selection (Phase 32, REBUILD_PLAN §7)."""

from __future__ import annotations

from datetime import UTC, datetime

from product_search.models import Listing
from product_search.selection import (
    apply_vendor_filter,
    select_for_display,
    vendor_key,
    vendor_matches_any,
)
from product_search.validators.price_sanity import FLAG_PRICE_ANOMALY_LOW


def _listing(
    price: float,
    seller: str,
    *,
    flags: list[str] | None = None,
    source: str = "serper_shopping",
) -> Listing:
    return Listing(
        source=source,
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


# ---------------------------------------------------------------------------
# Vendor allow/blocklist (Phase 33)
# ---------------------------------------------------------------------------


def test_vendor_matches_substring_of_merchant_name() -> None:
    # "walmart" matches the fuller Serper source string.
    assert vendor_matches_any(_listing(1.0, "Walmart - Seller"), ["Walmart"])
    assert vendor_matches_any(_listing(1.0, "B&H Photo-Video"), ["b&h"])
    assert not vendor_matches_any(_listing(1.0, "Newegg"), ["Walmart"])


def test_vendor_matches_ebay_marketplace_label() -> None:
    # An eBay listing's seller_name is a username; "eBay" still matches via the
    # marketplace label.
    ebay_lst = _listing(1.0, "randomseller123", source="ebay_search")
    assert vendor_matches_any(ebay_lst, ["eBay"])
    # A Serper result whose source string is literally "eBay" matches too.
    serper_ebay = _listing(1.0, "eBay")
    assert vendor_matches_any(serper_ebay, ["ebay"])


def test_vendor_filter_empty_lists_passthrough() -> None:
    listings = [_listing(1.0, "B&H"), _listing(2.0, "Walmart")]
    out = apply_vendor_filter(listings, allowlist=[], blocklist=[])
    assert out == listings


def test_vendor_allowlist_keeps_only_named() -> None:
    listings = [
        _listing(1.0, "B&H Photo"),
        _listing(2.0, "Walmart"),
        _listing(3.0, "Newegg"),
    ]
    out = apply_vendor_filter(listings, allowlist=["B&H", "Newegg"], blocklist=[])
    assert {lst.seller_name for lst in out} == {"B&H Photo", "Newegg"}


def test_vendor_blocklist_drops_ebay_and_named() -> None:
    listings = [
        _listing(1.0, "B&H"),
        _listing(2.0, "poshmark_seller", source="serper_shopping"),
        _listing(3.0, "username99", source="ebay_search"),
        _listing(4.0, "Poshmark"),
    ]
    out = apply_vendor_filter(listings, allowlist=[], blocklist=["eBay", "Poshmark"])
    # eBay marketplace listing dropped; "Poshmark" merchant dropped (both forms).
    assert {lst.seller_name for lst in out} == {"B&H"}


def test_vendor_allowlist_then_blocklist() -> None:
    listings = [
        _listing(1.0, "B&H"),
        _listing(2.0, "Walmart"),
        _listing(3.0, "Newegg"),
    ]
    # Allow B&H + Walmart, then block Walmart → only B&H survives.
    out = apply_vendor_filter(
        listings, allowlist=["B&H", "Walmart"], blocklist=["Walmart"]
    )
    assert {lst.seller_name for lst in out} == {"B&H"}
