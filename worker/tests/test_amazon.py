"""Tests for the DataForSEO Amazon Products recall adapter (Phase 38, ADR-141).

Fixture mode only — no live network. The committed Amazon fixtures are the raw
DataForSEO ``live/advanced`` responses captured during the Phase-38 spike.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from product_search.adapters import amazon
from product_search.models import AdapterQuery

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "amazon"
_DJI_FIXTURE = _FIXTURE_DIR / "dji_neo_2_motion_fly_more_combo.json"
_SUBSCRIPTION_FIXTURE = _FIXTURE_DIR / "the_week_magazine_subscription.json"


def _query() -> AdapterQuery:
    return AdapterQuery(source_id="amazon_dataforseo", queries=["DJI Neo 2 Motion Fly More Combo"])


def test_fetch_fixture_maps_core_fields() -> None:
    listings = amazon.fetch(_query(), fixture_path=_DJI_FIXTURE)
    assert listings, "fixture should yield listings"

    first = listings[0]
    # The ADAPTER id, not the merchant.
    assert first.source == "amazon_dataforseo"
    # Amazon yields a direct merchant link; url == buy_url (distinct-field hygiene).
    assert first.url.startswith("https://www.amazon.com")
    assert first.url == first.buy_url
    # ASIN carried in attrs (the stable Amazon id / enrichment seam).
    assert first.attrs["asin"]
    # Structured display fields mapped (rating from the nested object).
    assert first.rating is not None
    assert first.rating_count is not None
    # Real CDN image, never a data: URI.
    assert first.image_url and first.image_url.startswith("https://")
    # Honest unknowns (NOT "unknown" — would 100%-reject against condition_in:[new]).
    assert first.condition == ""
    assert first.brand is None
    assert first.quantity_available is None
    assert isinstance(first.fetched_at, datetime)


def test_fetch_records_real_cost() -> None:
    amazon.fetch(_query(), fixture_path=_DJI_FIXTURE)
    # The DJI spike envelope billed $0.0033 — surfaced verbatim (no fabrication).
    assert amazon.LAST_RUN_COST_USD == pytest.approx(0.0033)


def test_default_seller_is_amazon() -> None:
    listings = amazon.fetch(_query(), fixture_path=_DJI_FIXTURE)
    # Amazon search rows carry no seller string → default merchant label.
    assert all(lst.seller_name == "Amazon" for lst in listings)


def test_only_product_rows_are_mapped() -> None:
    """Non-product item rows (editorial/related-search) are skipped; only
    amazon_serp + amazon_paid become listings."""
    tasks = [
        {
            "status_code": 20000,
            "result": [
                {
                    "items": [
                        {"type": "amazon_serp", "data_asin": "A1", "title": "x", "url": "u1"},
                        {"type": "related_searches", "data_asin": None, "title": "noise"},
                        {"type": "amazon_paid", "data_asin": "A2", "title": "y", "url": "u2"},
                    ]
                }
            ],
        }
    ]
    listings = amazon._tasks_to_listings(tasks)
    assert {lst.attrs["asin"] for lst in listings} == {"A1", "A2"}


def test_dedup_by_asin() -> None:
    tasks = [
        {
            "status_code": 20000,
            "result": [
                {
                    "items": [
                        {"type": "amazon_serp", "data_asin": "A1", "title": "x", "url": "u1", "price_from": 10},
                        {"type": "amazon_serp", "data_asin": "A1", "title": "x dup", "url": "u2", "price_from": 11},
                        {"type": "amazon_serp", "data_asin": "A2", "title": "y", "url": "u3", "price_from": 12},
                    ]
                }
            ],
        }
    ]
    listings = amazon._tasks_to_listings(tasks)
    assert len(listings) == 2
    assert {lst.attrs["asin"] for lst in listings} == {"A1", "A2"}


def test_missing_price_is_zero_sentinel() -> None:
    listing = amazon._item_to_listing({"type": "amazon_serp", "data_asin": "A", "title": "no price"})
    assert listing.unit_price_usd == 0.0
    assert listing.price_usd == 0.0  # property alias
    assert listing.image_url is None
    assert listing.rating is None


def test_data_uri_image_is_dropped() -> None:
    listing = amazon._item_to_listing(
        {"type": "amazon_serp", "data_asin": "A", "title": "x", "image_url": "data:image/webp;base64,AAAA"}
    )
    assert listing.image_url is None


def test_task_level_error_raises_api_error(tmp_path: Path) -> None:
    """The spike gotcha: a top-level status_code 20000 can mask a task-level
    error — the adapter must check the task status, not just the envelope."""
    bad = {
        "status_code": 20000,
        "status_message": "Ok.",
        "tasks": [{"status_code": 40501, "status_message": "Invalid Field: 'language_name'", "result": None}],
    }
    f = tmp_path / "bad.json"
    f.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(amazon.AmazonAPIError):
        amazon.fetch(_query(), fixture_path=f)


def test_missing_creds_raises_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATAFORSEO_LOGIN", raising=False)
    monkeypatch.delenv("DATAFORSEO_PASSWORD", raising=False)
    monkeypatch.delenv("WORKER_USE_FIXTURES", raising=False)
    monkeypatch.setattr(amazon, "_env_files", lambda: [])
    with pytest.raises(amazon.AmazonAuthError):
        amazon.fetch(_query())


def test_subscription_recall_is_mostly_priceless() -> None:
    """Amazon back-issue noise for a subscription query mostly lacks a buy
    price — confirms the honest-degraded mapping (and why the onboarder leaves
    Amazon off for subscriptions). Prices that ARE present are real, not guessed."""
    listings = amazon.fetch(_query(), fixture_path=_SUBSCRIPTION_FIXTURE)
    assert listings
    priced = sum(1 for lst in listings if lst.unit_price_usd > 0.0)
    assert priced < len(listings)  # most rows have no buy price → 0.0 sentinel
