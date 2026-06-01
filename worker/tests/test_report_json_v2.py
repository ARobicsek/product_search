"""v2 JSON sidecar payload + markdown (Phase 32)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from product_search.models import Listing
from product_search.profile_v2 import load_profile_v2_from_path
from product_search.run_outcome import classify_run_outcome
from product_search.selection import SelectionResult
from product_search.synthesizer.report_json_v2 import build_v2_markdown, build_v2_payload

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "profiles_v2"
    / "dji-neo-2-motion-fly-more-combo"
    / "profile.yaml"
)


def _listing(price: float, seller: str) -> Listing:
    return Listing(
        source="serper_shopping",
        url="https://google.com/search?q=x",
        title="DJI Neo 2 Motion Fly More Combo",
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
        buy_url="https://google.com/shopping/x",
    )


def _payload() -> dict:
    profile = load_profile_v2_from_path(FIXTURE)
    displayed = [_listing(599.0, "B&H"), _listing(610.0, "Walmart")]
    selection = SelectionResult(
        displayed=displayed,
        overflow={"b&h": 1},
        hidden_anomalies=1,
    )
    outcome = classify_run_outcome(recall_count=40, survivor_count=3)
    return build_v2_payload(
        profile=profile,
        selection=selection,
        all_survivors=displayed + [_listing(620.0, "B&H")],
        columns=["price", "seller"],
        outcome=outcome,
        recall_count=40,
        survivor_count=3,
        run_calls=[],
        snapshot_date=date(2026, 5, 30),
    )


def test_payload_shape() -> None:
    p = _payload()
    assert p["schema_version"] == 2
    assert p["slug"] == "dji-neo-2-motion-fly-more-combo"
    assert p["product_type"] == "drone"
    assert p["columns"] == ["price", "seller"]
    assert len(p["listings"]) == 2
    assert p["listings"][0]["rank"] == 1
    assert p["listings"][0]["buy_url"] == "https://google.com/shopping/x"
    assert p["overflow"] == {"b&h": 1}
    assert p["hidden_anomalies"] == 1
    assert p["recall_count"] == 40
    assert p["survivor_count"] == 3
    assert p["displayed_count"] == 2
    assert len(p["all_listings"]) == 3  # displayed + 1 overflow B&H listing
    assert p["all_listings"][0]["rank"] == 1
    assert p["outcome"]["class"] == "ok"
    assert "total_usd" in p["run_cost"]


def test_run_cost_honors_flat_amazon_fee() -> None:
    """A flat-fee recall call (Amazon) carries an explicit real ``cost_usd``;
    the panel uses it directly instead of token-estimating (Phase 38 Step 5)."""
    from product_search.synthesizer.report_json_v2 import _build_run_cost

    run_calls = [
        {  # token-priced LLM call
            "step": "ai_filter",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "input_tokens": 1000,
            "output_tokens": 200,
        },
        {  # flat-fee Amazon recall
            "step": "amazon_recall",
            "provider": "dataforseo",
            "model": "amazon_products",
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0048,
        },
    ]
    rc = _build_run_cost(run_calls)
    amazon_step = next(s for s in rc["steps"] if s["step"] == "amazon_recall")
    assert amazon_step["cost_usd"] == 0.0048
    assert rc["any_unpriced"] is False
    # Total folds the flat fee in with the estimated LLM cost.
    assert rc["total_usd"] == pytest.approx(0.0048 + (1000 * 1.0 + 200 * 5.0) / 1_000_000)


def test_run_cost_prices_cached_filter_below_uncached() -> None:
    """ADR-142: an ai_filter step whose system block was cache-read is priced
    off real per-call cache usage (0.10x read, 1.25x write), well below the
    same tokens billed as fresh input. The split is surfaced on the step."""
    from product_search.synthesizer.report_json_v2 import _build_run_cost

    cached = _build_run_cost([
        {
            "step": "ai_filter",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "input_tokens": 2_000,
            "output_tokens": 1_000,
            "cache_creation_input_tokens": 16_000,
            "cache_read_input_tokens": 96_000,
        },
    ])
    uncached = _build_run_cost([
        {  # the same total input billed entirely as fresh tokens
            "step": "ai_filter",
            "provider": "anthropic",
            "model": "claude-haiku-4-5",
            "input_tokens": 2_000 + 16_000 + 96_000,
            "output_tokens": 1_000,
        },
    ])
    assert cached["total_usd"] < uncached["total_usd"]
    step = cached["steps"][0]
    # The split is surfaced honestly on the step (not hidden inside cost_usd).
    assert step["cache_read_input_tokens"] == 96_000
    assert step["cache_creation_input_tokens"] == 16_000
    assert cached["any_unpriced"] is False


def test_markdown_renders_table_and_overflow() -> None:
    md = build_v2_markdown(_payload())
    assert "DJI Neo 2 Motion Fly More Combo" in md
    assert "$599.00" in md
    assert "1 more from b&h" in md


def test_markdown_shows_outcome_note_when_not_ok() -> None:
    profile = load_profile_v2_from_path(FIXTURE)
    selection = SelectionResult(displayed=[], overflow={}, hidden_anomalies=0)
    outcome = classify_run_outcome(recall_count=0, survivor_count=0)
    payload = build_v2_payload(
        profile=profile,
        selection=selection,
        all_survivors=[],
        columns=["price"],
        outcome=outcome,
        recall_count=0,
        survivor_count=0,
        run_calls=[],
        snapshot_date=date(2026, 5, 30),
    )
    md = build_v2_markdown(payload)
    assert "No offers found" in md
