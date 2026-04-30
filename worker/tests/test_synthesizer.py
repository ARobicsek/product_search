"""Tests for the synthesizer (Phase 5).

We test the deterministic pieces — payload shaping and the post-check —
not the actual LLM call. The benchmark exercises real providers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from product_search.models import Listing
from product_search.profile import load_profile
from product_search.storage.diff import DiffResult, PriceChange
from product_search.synthesizer import (
    COLUMN_DEFS,
    DEFAULT_REPORT_COLUMNS,
    PostCheckError,
    build_input_payload,
    build_listings_table_md,
    post_check,
    render_prompt,
)


def _listing(
    url: str,
    *,
    title: str = "Test DDR5 RDIMM 32GB",
    unit_price_usd: float = 100.0,
    total_for_target_usd: float | None = 800.0,
    flags: list[str] | None = None,
) -> Listing:
    return Listing(
        source="ebay_search",
        url=url,
        title=title,
        fetched_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
        brand="Samsung",
        mpn="M321R4GA0BB0-CQK",
        attrs={"capacity_gb": 32, "speed_mts": 4800, "form_factor": "RDIMM"},
        condition="new",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=unit_price_usd,
        kit_price_usd=None,
        quantity_available=10,
        seller_name="some_seller",
        seller_rating_pct=99.5,
        seller_feedback_count=5000,
        ship_from_country="US",
        qvl_status="qvl",
        flags=flags or [],
        total_for_target_usd=total_for_target_usd,
    )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def test_prompt_file_exists_and_has_hard_rules() -> None:
    text = render_prompt()
    assert "Do NOT invent" in text
    assert "Do NOT modify any number" in text
    assert "Bottom line" in text


# ---------------------------------------------------------------------------
# Payload shaping
# ---------------------------------------------------------------------------


def test_payload_sorts_listings_by_total_for_target_nulls_last() -> None:
    profile = load_profile("ddr5-rdimm-256gb")
    listings = [
        _listing("https://x/a", total_for_target_usd=900.0),
        _listing("https://x/b", total_for_target_usd=None),
        _listing("https://x/c", total_for_target_usd=500.0),
        _listing("https://x/d", total_for_target_usd=700.0),
    ]
    payload = build_input_payload(listings, diff=None, profile=profile)
    urls = [row["url"] for row in payload["listings"]]
    assert urls == ["https://x/c", "https://x/d", "https://x/a", "https://x/b"]


def test_payload_diff_shape_includes_new_dropped_changed() -> None:
    profile = load_profile("ddr5-rdimm-256gb")
    diff = DiffResult(
        new=[_listing("https://x/new")],
        dropped=[_listing("https://x/old")],
        changed=[
            PriceChange(
                url="https://x/c",
                title="moved",
                old_price_usd=100.0,
                new_price_usd=80.0,
                pct_change=-0.20,
                new_listing=_listing("https://x/c", unit_price_usd=80.0),
            )
        ],
    )
    payload = build_input_payload([], diff=diff, profile=profile)
    assert payload["diff"] is not None
    assert len(payload["diff"]["new"]) == 1
    assert len(payload["diff"]["dropped"]) == 1
    assert payload["diff"]["changed"][0]["pct_change"] == pytest.approx(-0.20)


def test_payload_diff_is_none_for_first_run() -> None:
    profile = load_profile("ddr5-rdimm-256gb")
    payload = build_input_payload([_listing("https://x/a")], diff=None, profile=profile)
    assert payload["diff"] is None


# ---------------------------------------------------------------------------
# Post-check: pass cases
# ---------------------------------------------------------------------------


def _payload_one() -> dict[str, object]:
    profile = load_profile("ddr5-rdimm-256gb")
    return build_input_payload(
        [_listing("https://www.ebay.com/itm/123", unit_price_usd=120.0,
                  total_for_target_usd=960.0)],
        diff=None,
        profile=profile,
    )


def test_post_check_passes_clean_report() -> None:
    payload = _payload_one()
    report = (
        "**Bottom line.** 8x32GB at $120.0 each = $960.0.\n\n"
        "| Rank | Source | URL | Total |\n"
        "|---|---|---|---|\n"
        "| 1 | ebay_search | https://www.ebay.com/itm/123 | 960.0 |\n"
    )
    post_check(report, payload)  # no exception


def test_post_check_normalises_decimal_precision() -> None:
    """Report writes 120 (no decimals) but payload has 120.0 — should pass."""
    payload = _payload_one()
    report = "Price: $120 per module. Total: $960."
    post_check(report, payload)


def test_post_check_allows_rank_numbers() -> None:
    """Rank column 1..N should never trigger the post-check."""
    payload = _payload_one()
    report = "| 1 | row | https://www.ebay.com/itm/123 | 960.0 |\n| 2 | ... |"
    post_check(report, payload)


def test_post_check_allows_prompt_constants() -> None:
    """5% threshold and 200-word cap appear in the prompt, so allowed."""
    payload = _payload_one()
    report = "Within 200 words. Threshold is 5%."
    post_check(report, payload)


# ---------------------------------------------------------------------------
# Post-check: failure cases
# ---------------------------------------------------------------------------


def test_post_check_rejects_fabricated_price() -> None:
    payload = _payload_one()
    report = "Best price is $99.99 per module."  # 99.99 not in payload
    with pytest.raises(PostCheckError, match="fabricated numbers"):
        post_check(report, payload)


def test_post_check_rejects_fabricated_quantity() -> None:
    payload = _payload_one()
    # 47 is neither in payload nor a rank/constant.
    report = "Quantity available: 47 units."
    with pytest.raises(PostCheckError, match="fabricated numbers"):
        post_check(report, payload)


def test_post_check_failure_message_lists_offending_tokens() -> None:
    payload = _payload_one()
    report = "$77.77"
    with pytest.raises(PostCheckError) as excinfo:
        post_check(report, payload)
    msg = str(excinfo.value)
    assert "77.77" in msg


# ---------------------------------------------------------------------------
# Smoke: the bundled prompt file is actually present.
# ---------------------------------------------------------------------------


def test_prompt_file_lives_in_package() -> None:
    """The prompt is checked into the repo, per LLM_STRATEGY hard rule #3."""
    here = Path(__file__).parent.parent / "src" / "product_search" / "synthesizer" / "prompts"
    assert (here / "synth_v1.txt").is_file()


# ---------------------------------------------------------------------------
# Report-column registry / table builder (per-product columns)
# ---------------------------------------------------------------------------


def test_default_report_columns_match_legacy_table_shape() -> None:
    assert DEFAULT_REPORT_COLUMNS == [
        "rank", "source", "title", "price_unit", "total_for_target",
        "qty", "seller", "flags",
    ]
    for col in DEFAULT_REPORT_COLUMNS:
        assert col in COLUMN_DEFS


def test_table_uses_default_columns_when_none() -> None:
    md = build_listings_table_md([_listing("https://x/a")], None)
    assert "| Rank | Source | Title | Price (unit) | Total for target | Qty | Seller | Flags |" in md


def test_table_respects_custom_column_set_and_order() -> None:
    listing = _listing(
        "https://x/a",
        title="Bose NC700",
        unit_price_usd=99.95,
        total_for_target_usd=99.95,
    )
    md = build_listings_table_md(
        [listing],
        ["rank", "source", "title", "condition", "price_unit", "seller"],
    )
    assert "| Rank | Source | Title | Condition | Price (unit) | Seller |" in md
    assert "| 1 | [ebay_search](https://x/a) | Bose NC700 | new | $99.95 | some_seller |" in md


def test_table_drops_qty_and_includes_condition_for_headphone_use_case() -> None:
    listing = _listing(
        "https://x/a",
        title="Bose NC700 used",
        unit_price_usd=47.97,
        total_for_target_usd=47.97,
    )
    listing.condition = "used"
    md = build_listings_table_md([listing], ["rank", "title", "condition", "price_unit"])
    assert "| Rank | Title | Condition | Price (unit) |" in md
    assert "Qty" not in md
    assert "| used |" in md


def test_pipe_in_title_is_escaped_in_table() -> None:
    listing = _listing("https://x/a", title="Bose | NC700")
    md = build_listings_table_md([listing], None)
    assert "Bose \\| NC700" in md
