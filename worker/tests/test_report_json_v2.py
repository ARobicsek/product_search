"""v2 JSON sidecar payload + markdown (Phase 32)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

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
    selection = SelectionResult(
        displayed=[_listing(599.0, "B&H"), _listing(610.0, "Walmart")],
        overflow={"b&h": 1},
        hidden_anomalies=1,
    )
    outcome = classify_run_outcome(recall_count=40, survivor_count=3)
    return build_v2_payload(
        profile=profile,
        selection=selection,
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
    assert p["outcome"]["class"] == "ok"
    assert "total_usd" in p["run_cost"]


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
        columns=["price"],
        outcome=outcome,
        recall_count=0,
        survivor_count=0,
        run_calls=[],
        snapshot_date=date(2026, 5, 30),
    )
    md = build_v2_markdown(payload)
    assert "No offers found" in md
