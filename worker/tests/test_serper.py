"""Tests for the Serper.dev shopping recall adapter (Phase 31, ADR-133/134).

Fixture mode only — no live network. The committed Serper fixtures are the raw
``POST google.serper.dev/shopping`` responses captured during the Phase 30 spike.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from product_search.adapters import serper
from product_search.models import AdapterQuery
from product_search.profile import FilterRule
from product_search.validators.filters import reject_single_sku_url

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "serper" / "dji_neo2_fly_more_combo.json"
)


def _query() -> AdapterQuery:
    return AdapterQuery(source_id="serper_shopping", queries=["DJI Neo 2 Motion Fly More Combo"])


def test_fetch_fixture_maps_core_fields() -> None:
    listings = serper.fetch(_query(), fixture_path=_FIXTURE)
    assert listings, "fixture should yield listings"

    first = listings[0]
    # The ADAPTER id, not the merchant.
    assert first.source == "serper_shopping"
    # Merchant goes in seller_name.
    assert first.seller_name == "heliguy.com"
    # url AND buy_url are the same Serper google-shopping redirect (no fabrication).
    assert first.url == first.buy_url
    assert first.url.startswith("https://www.google.com/search?")
    # Structured display fields mapped.
    assert first.rating == 4.8
    assert first.rating_count == 3900
    # Price parsed from "$67.20".
    assert first.unit_price_usd == 67.20
    # price_usd property aliases unit_price_usd (REBUILD_PLAN §3).
    assert first.price_usd == first.unit_price_usd
    # Honest unknowns (NOT "unknown" — see _result_to_listing docstring / pitfall).
    assert first.condition == ""
    assert first.brand is None
    assert first.quantity_available is None
    # productId kept for future dedup / merchant resolution.
    assert first.attrs["serper_product_id"] == "14490624025270886420"
    assert isinstance(first.fetched_at, datetime)


def test_result_to_listing_maps_image_url() -> None:
    listing = serper._result_to_listing(
        {
            "title": "Widget",
            "source": "shop.example",
            "link": "https://www.google.com/search?q=widget",
            "price": "$1,234.50",
            "imageUrl": "https://img.example/widget.png",
            "rating": 4.2,
            "ratingCount": 12,
            "productId": "abc123",
        }
    )
    assert listing.image_url == "https://img.example/widget.png"
    assert listing.unit_price_usd == 1234.50
    assert listing.rating == 4.2
    assert listing.rating_count == 12


def test_result_to_listing_missing_price_is_zero_sentinel() -> None:
    listing = serper._result_to_listing(
        {"title": "No price", "source": "x", "link": "https://x", "productId": "p1"}
    )
    assert listing.unit_price_usd == 0.0
    assert listing.image_url is None
    assert listing.rating is None
    assert listing.rating_count is None


def test_dedup_by_product_id() -> None:
    results = [
        {"title": "A", "source": "s1", "link": "https://a", "productId": "dup"},
        {"title": "A again", "source": "s2", "link": "https://b", "productId": "dup"},
        {"title": "B", "source": "s3", "link": "https://c", "productId": "other"},
    ]
    listings = serper._results_to_listings(results)
    assert len(listings) == 2
    assert {ls.title for ls in listings} == {"A", "B"}


def test_dedup_falls_back_to_link_when_no_product_id() -> None:
    results = [
        {"title": "A", "source": "s1", "link": "https://same"},
        {"title": "A again", "source": "s2", "link": "https://same"},
        {"title": "B", "source": "s3", "link": "https://different"},
    ]
    listings = serper._results_to_listings(results)
    assert len(listings) == 2


def test_parse_price_variants() -> None:
    assert serper.parse_price("$1,299.99") == 1299.99
    assert serper.parse_price(49.5) == 49.5
    assert serper.parse_price(None) is None
    assert serper.parse_price("no digits here") is None


# ---------------------------------------------------------------------------
# Serper-aware single_sku_url (ADR-131 P0)
# ---------------------------------------------------------------------------


def test_single_sku_url_skips_serper_search_links() -> None:
    """A serper_shopping listing's google.com/search redirect must NOT be
    rejected by the single_sku_url rule (it is an offer, not a search page)."""
    rule = FilterRule(rule="single_sku_url")
    serper_listing = serper._result_to_listing(
        {
            "title": "DJI Neo 2",
            "source": "heliguy.com",
            "link": "https://www.google.com/search?ibp=oshop&q=DJI+Neo+2",
            "price": "$599",
            "productId": "x",
        }
    )
    assert reject_single_sku_url(serper_listing, rule) is None


def test_single_sku_url_still_rejects_non_serper_search_page() -> None:
    """The rule still fires for a non-serper source whose URL is a search page."""
    from tests.test_phase2 import _make_listing

    rule = FilterRule(rule="single_sku_url")
    ebay_search = _make_listing(
        source="ebay_search", url="https://www.ebay.com/sch/i.html?_nkw=ddr5"
    )
    assert reject_single_sku_url(ebay_search, rule) is not None
