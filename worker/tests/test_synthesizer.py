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
    FLAG_FALLBACK_DESCRIPTIONS,
    PostCheckError,
    build_bottom_line_md,
    build_flags_md,
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
    # Per ADR-028, the LLM only writes the Context paragraph; numeric
    # sections are deterministic. The prompt must enforce that boundary.
    assert "Context" in text
    assert "ABSOLUTELY NO DIGITS" in text
    assert "ABSOLUTELY NO CHAIN OF THOUGHT" in text


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


def test_post_check_error_carries_bad_numbers_for_retry() -> None:
    payload = _payload_one()
    report = "Saves 7.7% vs average."
    with pytest.raises(PostCheckError) as excinfo:
        post_check(report, payload)
    assert "7.7" in excinfo.value.bad_numbers


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


def test_source_column_renders_vendor_host_for_universal_ai() -> None:
    """The 'source' column shows the vendor host (without `www.`) for
    universal_ai_search rows so the user sees `audio46.com` instead of
    the literal adapter id. Internal `lst.source` stays canonical so
    source_stats / cost panel grouping is unaffected."""
    listing = _listing("https://www.audio46.com/products/bose-nc700", title="Bose 700")
    listing.source = "universal_ai_search"
    listing.attrs = {"vendor_host": "www.audio46.com"}
    md = build_listings_table_md([listing], ["rank", "source", "title"])
    assert "[audio46.com](https://www.audio46.com/products/bose-nc700)" in md
    assert "universal_ai_search" not in md


def test_source_column_falls_back_to_url_host_when_attr_missing() -> None:
    """Older Listings (pre-vendor_host attr) still render cleanly by
    parsing the URL host at render time."""
    listing = _listing("https://shop.example.com/p/widget", title="Widget")
    listing.source = "universal_ai_search"
    listing.attrs = {}
    md = build_listings_table_md([listing], ["rank", "source", "title"])
    assert "[shop.example.com](https://shop.example.com/p/widget)" in md


def test_source_column_unchanged_for_non_universal_adapters() -> None:
    """eBay etc. continue to display the adapter id verbatim."""
    listing = _listing("https://www.ebay.com/itm/123", title="Bose 700")
    md = build_listings_table_md([listing], ["rank", "source", "title"])
    assert "[ebay_search](https://www.ebay.com/itm/123)" in md


# ---------------------------------------------------------------------------
# Deterministic Bottom line (ADR-028)
# ---------------------------------------------------------------------------


def test_build_bottom_line_uses_cheapest_total_when_available() -> None:
    profile = load_profile("ddr5-rdimm-256gb")
    listings = [
        _listing("https://x/a", title="A", total_for_target_usd=900.0),
        _listing("https://x/b", title="B", total_for_target_usd=500.0),
        _listing("https://x/c", title="C", total_for_target_usd=700.0),
    ]
    md = build_bottom_line_md(listings, profile)
    assert md.startswith("**Bottom line.**")
    assert "$500.00" in md
    assert "B" in md
    # Other prices must NOT appear in the bottom line — only the cheapest.
    assert "$700.00" not in md
    assert "$900.00" not in md


def test_build_bottom_line_falls_back_to_unit_price_when_total_is_none() -> None:
    profile = load_profile("bose-nc-700-headphones")
    listing = _listing(
        "https://www.ebay.com/itm/127828108562",
        title="BOSE headphones NC700 USED",
        unit_price_usd=47.97,
        total_for_target_usd=None,
    )
    listing.condition = "used"
    md = build_bottom_line_md([listing], profile)
    assert "$47.97" in md
    # Used should be surfaced from listing.condition.
    assert "used" in md.lower()


def test_build_bottom_line_handles_empty_listings() -> None:
    profile = load_profile("ddr5-rdimm-256gb")
    md = build_bottom_line_md([], profile)
    assert "No listings" in md


def test_build_bottom_line_emits_clickable_source_link() -> None:
    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing("https://www.ebay.com/itm/123", total_for_target_usd=960.0)
    md = build_bottom_line_md([listing], profile)
    assert "[ebay_search](https://www.ebay.com/itm/123)" in md


# ---------------------------------------------------------------------------
# Deterministic Flags (ADR-028)
# ---------------------------------------------------------------------------


def test_build_flags_emits_no_flags_when_none_present() -> None:
    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing("https://x/a", flags=[])
    md = build_flags_md([listing], profile)
    assert md == "**Flags.** (no flags)"


def test_build_flags_uses_profile_description_when_present() -> None:
    """Profile-defined description wins over the fallback dict."""
    from product_search.profile import FlagRule, Profile

    base = load_profile("ddr5-rdimm-256gb")
    custom_rule = FlagRule(
        rule="low_seller_feedback",
        flag="low_seller_feedback",
        description="Custom description from profile",
    )
    profile = base.model_copy(update={"spec_flags": [custom_rule]})
    assert isinstance(profile, Profile)
    listing = _listing("https://x/a", flags=["low_seller_feedback"])
    md = build_flags_md([listing], profile)
    assert "Custom description from profile" in md
    # Fallback text must NOT appear when profile description is set.
    assert FLAG_FALLBACK_DESCRIPTIONS["low_seller_feedback"] not in md


def test_build_flags_falls_back_to_builtin_dict_when_profile_missing_description() -> None:
    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing("https://x/a", flags=["low_seller_feedback"])
    md = build_flags_md([listing], profile)
    assert FLAG_FALLBACK_DESCRIPTIONS["low_seller_feedback"] in md


def test_build_flags_dedupes_across_listings_and_sorts_stably() -> None:
    profile = load_profile("ddr5-rdimm-256gb")
    listings = [
        _listing("https://x/a", flags=["low_seller_feedback", "china_shipping"]),
        _listing("https://x/b", flags=["low_seller_feedback"]),
        _listing("https://x/c", flags=["china_shipping"]),
    ]
    md = build_flags_md(listings, profile)
    # Each unique flag appears exactly once.
    assert md.count("**low_seller_feedback**") == 1
    assert md.count("**china_shipping**") == 1
    # Sorted alphabetically — china_shipping appears before low_seller_feedback.
    assert md.index("china_shipping") < md.index("low_seller_feedback")


# ---------------------------------------------------------------------------
# synthesize() — LLM is responsible for Context only (ADR-028)
# ---------------------------------------------------------------------------


def test_synthesize_uses_only_context_from_llm_and_assembles_rest_deterministically() -> None:
    from unittest.mock import patch

    from product_search.llm import LLMResponse
    from product_search.synthesizer import synthesize

    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing(
        "https://www.ebay.com/itm/123",
        unit_price_usd=120.0,
        total_for_target_usd=960.0,
    )

    # The LLM emits ONLY a qualitative paragraph — no headers, no numbers
    # except those that appear in the payload.
    llm_paragraph = (
        "Today's market is dominated by a single seller offering the "
        "cheapest path; the remaining listings cluster at the higher end."
    )
    resp = LLMResponse(
        provider="glm",
        model="glm-4.5-flash",
        text=llm_paragraph,
        input_tokens=1,
        output_tokens=1,
    )
    with patch(
        "product_search.synthesizer.synthesizer.call_llm",
        side_effect=lambda **kw: resp,
    ):
        result = synthesize(
            [listing], None, profile, provider="glm", model="glm-4.5-flash"
        )

    md = result.report_md
    assert "**Bottom line.**" in md
    assert "$960.00" in md  # deterministic Bottom line
    assert "**Ranked listings.**" in md
    assert "**Diff vs yesterday.**" in md
    assert "**Flags.**" in md
    assert "**Context.**" in md
    assert llm_paragraph in md


def test_synthesize_strips_redundant_context_prefix_from_llm() -> None:
    """Even if the LLM disobeys the prompt and prefixes 'Context.' the
    deterministic layer should normalise it."""
    from unittest.mock import patch

    from product_search.llm import LLMResponse
    from product_search.synthesizer import synthesize

    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing("https://x/a", total_for_target_usd=960.0)

    resp = LLMResponse(
        provider="glm",
        model="glm-4.5-flash",
        text="**Context.** Most listings come from familiar sellers.",
        input_tokens=1,
        output_tokens=1,
    )
    with patch(
        "product_search.synthesizer.synthesizer.call_llm",
        side_effect=lambda **kw: resp,
    ):
        result = synthesize(
            [listing], None, profile, provider="glm", model="glm-4.5-flash"
        )
    # The Context header appears exactly once (the deterministic one).
    assert result.report_md.count("**Context.**") == 1


def test_synthesize_retries_once_when_llm_fabricates_in_context() -> None:
    from unittest.mock import patch

    from product_search.llm import LLMResponse
    from product_search.synthesizer import synthesize

    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing(
        "https://x/a", unit_price_usd=120.0, total_for_target_usd=960.0
    )

    bad = LLMResponse(
        provider="glm",
        model="glm-4.5-flash",
        text="The cheapest entry saves 7.7% versus the average.",
        input_tokens=1,
        output_tokens=1,
    )
    good = LLMResponse(
        provider="glm",
        model="glm-4.5-flash",
        text="The cheapest entry sits well below the rest of the field.",
        input_tokens=1,
        output_tokens=1,
    )
    responses = iter([bad, good])
    with patch(
        "product_search.synthesizer.synthesizer.call_llm",
        side_effect=lambda **kw: next(responses),
    ):
        result = synthesize(
            [listing], None, profile, provider="glm", model="glm-4.5-flash"
        )
    assert "well below" in result.report_md


def test_synthesize_propagates_error_when_retry_also_fabricates() -> None:
    from unittest.mock import patch

    from product_search.llm import LLMResponse
    from product_search.synthesizer import synthesize

    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing(
        "https://x/a", unit_price_usd=120.0, total_for_target_usd=960.0
    )

    bad = LLMResponse(
        provider="glm",
        model="glm-4.5-flash",
        text="The cheapest entry saves 7.7% versus the average.",
        input_tokens=1,
        output_tokens=1,
    )
    with patch(
        "product_search.synthesizer.synthesizer.call_llm",
        side_effect=lambda **kw: bad,
    ):
        with pytest.raises(PostCheckError):
            synthesize(
                [listing], None, profile, provider="glm", model="glm-4.5-flash"
            )
