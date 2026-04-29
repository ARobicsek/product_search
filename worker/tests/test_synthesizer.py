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
    PostCheckError,
    build_input_payload,
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
    # Section headers we rely on for the bar criteria
    assert "Bottom line" in text
    assert "Ranked listings" in text
    assert "Diff vs yesterday" in text


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


def test_post_check_rejects_fabricated_url() -> None:
    payload = _payload_one()
    report = "See https://example.com/not-in-input for details."
    with pytest.raises(PostCheckError, match="fabricated URLs"):
        post_check(report, payload)


def test_post_check_accepts_url_with_extra_query_params() -> None:
    """Real eBay URLs come with tracking query params; post-check uses
    canonical (scheme+host+path) match per ADR-020 so the same item with
    different tracking strings still passes."""
    payload = build_input_payload(
        [
            _listing(
                "https://www.ebay.com/itm/267646680423",
                unit_price_usd=120.0,
                total_for_target_usd=960.0,
            )
        ],
        diff=None,
        profile=load_profile("ddr5-rdimm-256gb"),
    )
    # Same item, but the LLM emits the URL with eBay's full tracking string.
    report = (
        "**Bottom line.** $120.0 each = $960.0.\n\n"
        "| 1 | ebay_search | https://www.ebay.com/itm/267646680423?_skw=DDR5"
        "+4800&hash=item3e50fc3d67:g:abc&amdata=enc%3AAQAL... | 960.0 |"
    )
    post_check(report, payload)


def test_post_check_rejects_url_with_different_path() -> None:
    """Same host but different item id is still a fabrication."""
    payload = build_input_payload(
        [
            _listing(
                "https://www.ebay.com/itm/111",
                unit_price_usd=120.0,
                total_for_target_usd=960.0,
            )
        ],
        diff=None,
        profile=load_profile("ddr5-rdimm-256gb"),
    )
    report = "See https://www.ebay.com/itm/222 for details."
    with pytest.raises(PostCheckError, match="fabricated URLs"):
        post_check(report, payload)


def test_post_check_rejects_fabricated_quantity() -> None:
    payload = _payload_one()
    # 47 is neither in payload nor a rank/constant.
    report = "Quantity available: 47 units."
    with pytest.raises(PostCheckError, match="fabricated numbers"):
        post_check(report, payload)


def test_post_check_failure_message_lists_offending_tokens() -> None:
    payload = _payload_one()
    report = "$77.77 at https://nope.example/x"
    with pytest.raises(PostCheckError) as excinfo:
        post_check(report, payload)
    msg = str(excinfo.value)
    assert "77.77" in msg
    assert "https://nope.example/x" in msg


# ---------------------------------------------------------------------------
# Smoke: the bundled prompt file is actually present.
# ---------------------------------------------------------------------------


def test_prompt_file_lives_in_package() -> None:
    """The prompt is checked into the repo, per LLM_STRATEGY hard rule #3."""
    here = Path(__file__).parent.parent / "src" / "product_search" / "synthesizer" / "prompts"
    assert (here / "synth_v1.txt").is_file()
