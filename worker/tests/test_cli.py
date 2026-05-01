"""Tests for ``product_search.cli`` helpers that don't need a full CLI run.

Currently scoped to ``_passed_match_key`` — the per-listing key the CLI uses to
attribute pipeline-passed listings back to a specific source-stats row. The
direct motivation is the bug where multiple ``universal_ai_search`` source
entries (one per vendor URL) all shared the same canonical ``source`` field on
their listings, causing every universal_ai source row to claim the full
universal_ai_search passed-count (e.g. bhphotovideo showing 0 fetched / 3 passed
because backmarket's listings got attributed to it too).
"""

from __future__ import annotations

from datetime import UTC, datetime

from product_search.cli import _passed_match_key
from product_search.models import Listing


def _make_listing(source: str, attrs: dict | None = None) -> Listing:
    """Build a minimally-populated Listing for key tests."""
    return Listing(
        source=source,
        url="https://example.com/p/x",
        title="x",
        fetched_at=datetime.now(tz=UTC),
        brand=None,
        mpn=None,
        attrs=attrs or {},
        condition="new",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=1.0,
        kit_price_usd=None,
        quantity_available=None,
        seller_name="x",
        seller_rating_pct=None,
        seller_feedback_count=None,
        ship_from_country=None,
    )


def test_passed_match_key_non_universal_keys_by_source_only() -> None:
    lst = _make_listing("ebay_search")
    assert _passed_match_key(lst) == ("ebay_search", None)


def test_passed_match_key_universal_keys_by_vendor_host() -> None:
    lst = _make_listing("universal_ai_search", {"vendor_host": "backmarket.com"})
    assert _passed_match_key(lst) == ("universal_ai_search", "backmarket.com")


def test_passed_match_key_strips_www_prefix() -> None:
    # The cli's source_stats row stores the host already-stripped of "www.";
    # the listing-side key must match that shape so the join works.
    lst = _make_listing("universal_ai_search", {"vendor_host": "www.bhphotovideo.com"})
    assert _passed_match_key(lst) == ("universal_ai_search", "bhphotovideo.com")


def test_passed_match_key_lowercases_host() -> None:
    lst = _make_listing("universal_ai_search", {"vendor_host": "BackMarket.com"})
    assert _passed_match_key(lst) == ("universal_ai_search", "backmarket.com")


def test_passed_match_key_universal_without_host_falls_back_to_none() -> None:
    # Older universal_ai listings that pre-date the vendor_host attr would
    # group under the "no host" bucket; this is acceptable since the source
    # row in that case would also have match_host=None.
    lst = _make_listing("universal_ai_search", {})
    assert _passed_match_key(lst) == ("universal_ai_search", None)


def test_passed_match_key_disambiguates_two_universal_vendors() -> None:
    # The actual bug repro: two listings, two vendors, two distinct keys.
    a = _make_listing("universal_ai_search", {"vendor_host": "backmarket.com"})
    b = _make_listing("universal_ai_search", {"vendor_host": "bhphotovideo.com"})
    assert _passed_match_key(a) != _passed_match_key(b)
