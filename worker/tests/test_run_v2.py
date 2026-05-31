"""v2 run pipeline orchestration (Phase 32)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from product_search.models import Listing
from product_search.profile_v2 import ProfileV2, load_profile_v2_from_path
from product_search.run_outcome import RunOutcomeClass
from product_search.run_v2 import run_v2, run_v2_pipeline

PROFILES_V2_DIR = Path(__file__).parent / "fixtures" / "profiles_v2"
FIXTURE = PROFILES_V2_DIR / "dji-neo-2-motion-fly-more-combo" / "profile.yaml"


def _passthrough(listings: list[Listing], _profile: object) -> list[Listing]:
    """Stand-in ai_filter: keeps every listing (no LLM, no network)."""
    return list(listings)


def _listing(
    price: float,
    *,
    title: str = "DJI Neo 2 Motion Fly More Combo",
    seller: str = "B&H",
    condition: str = "",
) -> Listing:
    return Listing(
        source="serper_shopping",
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


def test_pipeline_drops_excluded_titles_and_hides_anomaly() -> None:
    profile = load_profile_v2_from_path(FIXTURE)
    result = run_v2_pipeline(profile, _recall_set(), ai_filter_fn=_passthrough)

    assert result.recall_count == 7
    # The two title_excludes listings are dropped deterministically.
    assert all("Refurbished" not in lst.title for lst in result.survivors)
    assert all("Open Box" not in lst.title for lst in result.survivors)
    assert len(result.survivors) == 5

    # The $67.20 anomaly is hidden from display but kept in survivors/history.
    assert result.selection.hidden_anomalies == 1
    assert any(lst.price_usd == 67.2 for lst in result.survivors)
    assert all(lst.price_usd != 67.2 for lst in result.selection.displayed)

    # Cheapest-first, anomaly never #1.
    assert result.selection.displayed[0].price_usd == 599.0
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
