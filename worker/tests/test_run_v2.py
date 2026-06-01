"""v2 run pipeline orchestration (Phase 32)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from product_search.models import Listing
from product_search.profile_v2 import ProfileV2, load_profile_v2_from_path
from product_search.run_outcome import RunOutcomeClass
from product_search.run_v2 import _default_recall, run_v2, run_v2_pipeline

PROFILES_V2_DIR = Path(__file__).parent / "fixtures" / "profiles_v2"
FIXTURE = PROFILES_V2_DIR / "dji-neo-2-motion-fly-more-combo" / "profile.yaml"


def _passthrough(listings: list[Listing], profile: Any, display_attrs: Any = None) -> list[Listing]:
    """A no-op ai_filter_fn for testing the pure pipeline core."""
    return list(listings)


def _listing(
    price: float,
    *,
    title: str = "DJI Neo 2 Motion Fly More Combo",
    seller: str = "B&H",
    condition: str = "",
    source: str = "serper_shopping",
) -> Listing:
    return Listing(
        source=source,
        url="https://google.com/search?q=x",
        title=title,
        fetched_at=datetime.now(tz=UTC),
        brand=None,
        mpn=None,
        attrs={},
        condition=condition,
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=price,
        kit_price_usd=None,
        quantity_available=None,
        seller_name=seller,
        seller_rating_pct=None,
        seller_feedback_count=None,
        ship_from_country=None,
    )


def _recall_set() -> list[Listing]:
    # Fixture title_excludes: Refurbished / Used / Open Box / Renewed.
    return [
        _listing(599.0, seller="B&H"),
        _listing(610.0, seller="Walmart"),
        _listing(605.0, seller="Newegg"),
        _listing(620.0, seller="Adorama"),
        _listing(67.2, seller="ScamShop"),                              # price anomaly
        _listing(540.0, title="DJI Neo 2 Combo Refurbished", seller="X"),  # excluded
        _listing(550.0, title="DJI Neo 2 Combo Open Box", seller="Y"),     # excluded
    ]


def test_pipeline_drops_excluded_titles_and_sequesters_anomaly() -> None:
    profile = load_profile_v2_from_path(FIXTURE)
    result = run_v2_pipeline(profile, _recall_set(), ai_filter_fn=_passthrough)

    assert result.recall_count == 7
    # The two title_excludes listings are dropped deterministically.
    assert all("Refurbished" not in lst.title for lst in result.survivors)
    assert all("Open Box" not in lst.title for lst in result.survivors)
    assert len(result.survivors) == 5

    # The $67.20 anomaly is sequestered to the bottom.
    assert result.selection.hidden_anomalies == 0
    assert any(lst.price_usd == 67.2 for lst in result.survivors)
    
    # Cheapest-first, anomaly never #1.
    assert result.selection.displayed[0].price_usd == 599.0
    assert result.selection.displayed[-1].price_usd == 67.2
    assert result.outcome.klass is RunOutcomeClass.OK


def test_pipeline_type_aware_columns() -> None:
    profile = load_profile_v2_from_path(FIXTURE)
    result = run_v2_pipeline(profile, _recall_set(), ai_filter_fn=_passthrough)
    # seller is populated; seller_rating is None for all → dropped; condition is
    # "" for all serper listings → dropped. price always present.
    assert "price" in result.columns
    assert "seller" in result.columns
    assert "seller_rating" not in result.columns
    assert "condition" not in result.columns


def test_pipeline_no_recall() -> None:
    profile = load_profile_v2_from_path(FIXTURE)
    result = run_v2_pipeline(profile, [], ai_filter_fn=_passthrough)
    assert result.outcome.klass is RunOutcomeClass.NO_RECALL
    assert result.selection.displayed == []


def test_pipeline_degraded_attr_on_min_quantity() -> None:
    raw = {
        "schema_version": 2,
        "slug": "x-prod",
        "display_name": "X Prod",
        "target": {"unit": "count", "amount": 1},
        "queries": ["x prod"],
        "filters": {"min_quantity": 10},
    }
    profile = ProfileV2.model_validate(raw)
    result = run_v2_pipeline(profile, [_listing(50.0)], ai_filter_fn=_passthrough)
    assert result.degraded_attrs is True
    assert any(c == "degraded_attr" for c, _ in result.outcome.notes)


def test_pipeline_vendor_blocklist_drops_vendor() -> None:
    raw = {
        "schema_version": 2,
        "slug": "x-prod",
        "display_name": "DJI Neo 2 Motion Fly More Combo",
        "target": {"unit": "count", "amount": 1},
        "queries": ["DJI Neo 2 Motion Fly More Combo"],
        "vendor_blocklist": ["eBay"],
    }
    profile = ProfileV2.model_validate(raw)
    blocked = _listing(540.0, seller="someseller", source="ebay_search")
    # Real itm URL so, absent the blocklist, it would pass single_sku_url and
    # survive — the test then genuinely exercises the blocklist, not the URL gate.
    blocked.url = "https://www.ebay.com/itm/999"
    recall = [_listing(599.0, seller="B&H"), blocked]
    result = run_v2_pipeline(profile, recall, ai_filter_fn=_passthrough)
    # The eBay marketplace listing is dropped before display despite being
    # the cheapest.
    assert all(lst.source != "ebay_search" for lst in result.survivors)
    assert result.selection.displayed[0].seller_name == "B&H"


# ---------------------------------------------------------------------------
# Recall orchestration (Phase 33 — Serper + eBay union/dedup, per-source errors)
# ---------------------------------------------------------------------------


def _both_sources_profile() -> ProfileV2:
    return ProfileV2.model_validate(
        {
            "schema_version": 2,
            "slug": "x-prod",
            "display_name": "X Prod",
            "target": {"unit": "count", "amount": 1},
            "queries": ["x prod"],
            "sources": {"serper": {"enabled": True}, "ebay": {"enabled": True}},
        }
    )


def test_default_recall_unions_serper_and_ebay(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.adapters import ebay, serper

    s = _listing(100.0, seller="Walmart")
    s.url = "https://google.com/search?q=a"
    e = _listing(90.0, seller="ebayuser", source="ebay_search")
    e.url = "https://www.ebay.com/itm/1"
    monkeypatch.setattr(serper, "fetch", lambda _q: [s])
    monkeypatch.setattr(ebay, "fetch", lambda _q: [e])

    outcome = _default_recall(_both_sources_profile())
    assert len(outcome.listings) == 2
    assert outcome.serper_error is False
    assert outcome.ebay_error is False


def test_default_recall_dedups_by_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.adapters import ebay, serper

    a = _listing(100.0, seller="Walmart")
    a.url = "https://www.ebay.com/itm/dupe"
    b = _listing(100.0, seller="ebayuser", source="ebay_search")
    b.url = "https://www.ebay.com/itm/dupe"
    monkeypatch.setattr(serper, "fetch", lambda _q: [a])
    monkeypatch.setattr(ebay, "fetch", lambda _q: [b])

    outcome = _default_recall(_both_sources_profile())
    assert len(outcome.listings) == 1  # collapsed by shared URL


def test_default_recall_skips_disabled_ebay(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.adapters import ebay, serper

    called = {"ebay": False}

    def _ebay_fetch(_q: object) -> list[Listing]:
        called["ebay"] = True
        return []

    monkeypatch.setattr(serper, "fetch", lambda _q: [_listing(100.0)])
    monkeypatch.setattr(ebay, "fetch", _ebay_fetch)

    profile = ProfileV2.model_validate(
        {
            "schema_version": 2,
            "slug": "x-prod",
            "display_name": "X Prod",
            "target": {"unit": "count", "amount": 1},
            "queries": ["x prod"],
            "sources": {"serper": {"enabled": True}, "ebay": {"enabled": False}},
        }
    )
    outcome = _default_recall(profile)
    assert called["ebay"] is False
    assert len(outcome.listings) == 1


def test_default_recall_captures_ebay_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.adapters import ebay, serper
    from product_search.adapters.ebay import EbayAuthError

    def _ebay_fetch(_q: object) -> list[Listing]:
        raise EbayAuthError("no creds")

    monkeypatch.setattr(serper, "fetch", lambda _q: [_listing(100.0)])
    monkeypatch.setattr(ebay, "fetch", _ebay_fetch)

    outcome = _default_recall(_both_sources_profile())
    assert outcome.ebay_error is True
    assert outcome.serper_error is False
    assert len(outcome.listings) == 1  # Serper still came back


def _amazon_profile() -> ProfileV2:
    return ProfileV2.model_validate(
        {
            "schema_version": 2,
            "slug": "x-prod",
            "display_name": "X Prod",
            "target": {"unit": "count", "amount": 1},
            "queries": ["x prod"],
            "sources": {
                "serper": {"enabled": True},
                "amazon": {"enabled": True},
            },
        }
    )


def test_default_recall_includes_amazon(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.adapters import amazon, serper

    s = _listing(100.0, seller="Walmart")
    s.url = "https://google.com/search?q=a"
    a = _listing(90.0, seller="Amazon", source="amazon_dataforseo")
    a.url = "https://www.amazon.com/dp/B0TEST"
    monkeypatch.setattr(serper, "fetch", lambda _q: [s])
    monkeypatch.setattr(amazon, "fetch", lambda _q: [a])
    monkeypatch.setattr(amazon, "LAST_RUN_COST_USD", 0.005)

    outcome = _default_recall(_amazon_profile())
    assert len(outcome.listings) == 2
    assert outcome.amazon_error is False
    assert outcome.amazon_cost_usd == 0.005


def test_default_recall_skips_disabled_amazon(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.adapters import amazon, serper

    called = {"amazon": False}

    def _amazon_fetch(_q: object) -> list[Listing]:
        called["amazon"] = True
        return []

    monkeypatch.setattr(serper, "fetch", lambda _q: [_listing(100.0)])
    monkeypatch.setattr(amazon, "fetch", _amazon_fetch)

    # _both_sources_profile leaves amazon at its default (disabled).
    outcome = _default_recall(_both_sources_profile())
    assert called["amazon"] is False
    assert outcome.amazon_cost_usd is None


def test_default_recall_captures_amazon_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.adapters import amazon, serper
    from product_search.adapters.amazon import AmazonAPIError

    def _amazon_fetch(_q: object) -> list[Listing]:
        raise AmazonAPIError("40104 verify your account")

    monkeypatch.setattr(serper, "fetch", lambda _q: [_listing(100.0)])
    monkeypatch.setattr(amazon, "fetch", _amazon_fetch)

    outcome = _default_recall(_amazon_profile())
    assert outcome.amazon_error is True
    assert outcome.serper_error is False
    assert len(outcome.listings) == 1  # Serper still came back


def test_pipeline_threads_amazon_error_to_note() -> None:
    profile = load_profile_v2_from_path(FIXTURE)
    result = run_v2_pipeline(
        profile, _recall_set(), ai_filter_fn=_passthrough, amazon_error=True
    )
    assert any(c == "amazon_unavailable" for c, _ in result.outcome.notes)


def test_default_recall_captures_serper_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from product_search.adapters import ebay, serper
    from product_search.adapters.serper import SerperAPIError

    def _serper_fetch(_q: object) -> list[Listing]:
        raise SerperAPIError("502")

    monkeypatch.setattr(serper, "fetch", _serper_fetch)
    monkeypatch.setattr(
        ebay, "fetch", lambda _q: [_listing(90.0, seller="u", source="ebay_search")]
    )

    outcome = _default_recall(_both_sources_profile())
    assert outcome.serper_error is True
    assert outcome.ebay_error is False
    assert len(outcome.listings) == 1


def test_run_v2_wrapper_writes_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRODUCT_SEARCH_PRODUCTS_DIR", str(PROFILES_V2_DIR))
    monkeypatch.setenv("WORKER_USE_FIXTURES", "1")  # ai_filter passthrough
    # The report path is rooted at the repo via synthesizer.report._repo_root
    # (it does NOT honor PRODUCT_SEARCH_REPORTS_DIR — that override only
    # redirects the ai_filter log). Redirect the repo root so the run writes
    # under tmp_path/reports instead of polluting the working tree.
    import product_search.synthesizer.report as report_mod

    monkeypatch.setattr(report_mod, "_repo_root", lambda: tmp_path)

    run_v2(
        "dji-neo-2-motion-fly-more-combo",
        no_store=True,
        recall_fn=lambda _p: _recall_set(),
    )

    sidecars = list(
        (tmp_path / "reports" / "dji-neo-2-motion-fly-more-combo").glob("*.json")
    )
    assert sidecars, "expected a JSON sidecar to be written"
