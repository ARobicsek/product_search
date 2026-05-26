"""Tests for the synthesizer (Phase 5).

We test the deterministic pieces — payload shaping and the post-check —
not the actual LLM call. The benchmark exercises real providers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from product_search.models import Listing
from product_search.storage.diff import DiffResult, PriceChange
from tests.conftest import load_ddr5_profile as load_profile
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


def test_default_report_columns_match_table_shape() -> None:
    # Updated 2026-05-25 (ADR-094): `price` replaced `price_unit` in the
    # default set so subscriptions don't surface a derived per-issue rate
    # (e.g. $179÷52=$3.44) as the headline price column. The legacy
    # `price_unit` is still available as an explicit column id for RAM
    # profiles and multi-pack consumer goods that DO want per-stick
    # comparison.
    assert DEFAULT_REPORT_COLUMNS == [
        "rank", "source", "title", "price", "total_for_target",
        "qty", "seller", "flags",
    ]
    for col in DEFAULT_REPORT_COLUMNS:
        assert col in COLUMN_DEFS


def test_table_uses_default_columns_when_none() -> None:
    md = build_listings_table_md([_listing("https://x/a")], None)
    assert "| Rank | Source | Title | Price | Total for target | Qty | Seller | Flags |" in md


def test_price_column_shows_kit_price_for_kit_subscription() -> None:
    """The `price` column (ADR-094) renders the as-sold price: kit_price
    for kits, unit_price for non-kits. For a 52-issue subscription kit at
    $199 with derived per-issue $3.83, the cell MUST read $199.00 — not
    the per-issue rate that pre-ADR-094 `price_unit` would have shown.
    """
    listing = _listing(
        "https://www.magazineline.com/the-week-printdigital-magazine",
        title="The Week Print+Digital Magazine Subscription",
        unit_price_usd=3.83,
    )
    listing.is_kit = True
    listing.kit_module_count = 52
    listing.kit_price_usd = 199.0
    md = build_listings_table_md([listing], ["rank", "title", "price"])
    assert "| Rank | Title | Price |" in md
    assert "| $199.00 |" in md
    assert "$3.83" not in md  # the misleading per-issue rate must not appear


def test_price_column_falls_back_to_unit_price_for_non_kit() -> None:
    """For a non-kit consumer good (is_kit=False), the `price` column
    shows unit_price_usd unchanged.
    """
    listing = _listing(
        "https://www.dyson.com/v15",
        title="Dyson V15 Detect Cordless Vacuum",
        unit_price_usd=749.99,
    )
    listing.is_kit = False
    listing.kit_module_count = 1
    listing.kit_price_usd = None
    md = build_listings_table_md([listing], ["rank", "title", "price"])
    assert "| Rank | Title | Price |" in md
    assert "| $749.99 |" in md


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


def test_build_bottom_line_uses_vendor_host_for_universal_ai() -> None:
    """The Bottom line must show the vendor host (e.g. provantage.com), not
    the literal `universal_ai_search` adapter id, mirroring the Source column
    (regression: the id used to leak into the headline)."""
    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing(
        "https://www.provantage.com/amd-100-000000694~7AAMD3R8.htm",
        total_for_target_usd=960.0,
    )
    listing.source = "universal_ai_search"
    listing.attrs = {"vendor_host": "www.provantage.com"}
    md = build_bottom_line_md([listing], profile)
    assert "universal_ai_search" not in md
    assert "[provantage.com](https://www.provantage.com/amd-100-000000694~7AAMD3R8.htm)" in md


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


def test_build_flags_falls_back_via_rule_name_when_flag_label_differs() -> None:
    """ADR-095 paper-cut B regression.

    The onboarder canonically emits ``rule: low_seller_feedback`` with
    ``flag: low_feedback`` (a *different* label). Before the fix the
    fallback dict missed because it was keyed by the rule name, and every
    live report rendered ``- **low_feedback**: (no description)``. The
    renderer now walks ``profile_desc[flag] → fallback[flag] →
    fallback[rule_of_flag]`` so the built-in description still surfaces.
    """
    from product_search.profile import FlagRule, Profile

    base = load_profile("ddr5-rdimm-256gb")
    rule = FlagRule(rule="low_seller_feedback", flag="low_feedback")  # no description
    profile = base.model_copy(update={"spec_flags": [rule]})
    assert isinstance(profile, Profile)
    listing = _listing("https://x/a", flags=["low_feedback"])
    md = build_flags_md([listing], profile)
    # The built-in description from FLAG_FALLBACK_DESCRIPTIONS["low_seller_feedback"]
    # must surface even though the flag *label* is "low_feedback".
    assert FLAG_FALLBACK_DESCRIPTIONS["low_seller_feedback"] in md
    assert "(no description)" not in md
    assert "**low_feedback**" in md


def test_build_flags_renders_bare_when_no_description_anywhere() -> None:
    """Unknown flags with no description render bare (no '(no description)' placeholder).

    Pinned because the literal ``(no description)`` placeholder is
    user-visible noise. If a profile declares a custom flag without a
    description and the flag id isn't in the fallback dict, the right
    move is to render the bare label — the listings table already
    surfaces *that* the flag fired; the legend bullet only adds value
    when it can explain *why*.
    """
    from product_search.profile import FlagRule, Profile

    base = load_profile("ddr5-rdimm-256gb")
    rule = FlagRule(rule="title_mentions", flag="some_custom_flag", values=["foo"])
    profile = base.model_copy(update={"spec_flags": [rule]})
    assert isinstance(profile, Profile)
    listing = _listing("https://x/a", flags=["some_custom_flag"])
    md = build_flags_md([listing], profile)
    assert "**some_custom_flag**" in md
    assert "(no description)" not in md
    # Bare bullet — no colon since there's no description following.
    assert "- **some_custom_flag**\n" in md + "\n" or md.rstrip().endswith("**some_custom_flag**")


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
# synthesize() — deterministic Ranked-listings markdown (ADR-096)
# ---------------------------------------------------------------------------
#
# Per ADR-096 the synth LLM call is retired: synthesize() returns only the
# Ranked-listings markdown table. Bottom line / Diff / Flags-legend /
# Context have been removed from the output (the React UI consumes the
# JSON sidecar; the markdown is the legacy-renderer fallback only).


def test_synthesize_emits_only_the_ranked_listings_table() -> None:
    """ADR-096: synth markdown is just the listings table now."""
    from product_search.synthesizer import synthesize

    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing(
        "https://www.ebay.com/itm/123",
        unit_price_usd=120.0,
        total_for_target_usd=960.0,
    )

    result = synthesize([listing], None, profile)
    md = result.report_md

    assert "**Ranked listings.**" in md
    # Sections retired by ADR-096 must NOT appear in the output.
    assert "**Bottom line.**" not in md
    assert "**Diff vs yesterday.**" not in md
    assert "**Flags.**" not in md
    assert "**Context.**" not in md


def test_synthesize_signature_drops_provider_and_model() -> None:
    """ADR-096 removes the LLM-call kwargs from synthesize().

    A regression that re-introduced ``provider=`` / ``model=`` would
    silently re-enable an LLM round-trip nobody wants — pin the shape.
    """
    import inspect

    from product_search.synthesizer import synthesize

    sig = inspect.signature(synthesize)
    assert "provider" not in sig.parameters
    assert "model" not in sig.parameters
    assert "max_tokens" not in sig.parameters


def test_synthesis_result_carries_only_report_md() -> None:
    """ADR-096: SynthesisResult is reduced to the markdown payload —
    no more provider/model/tokens fields (synth LLM retired)."""
    from dataclasses import fields

    from product_search.synthesizer import SynthesisResult

    field_names = {f.name for f in fields(SynthesisResult)}
    assert field_names == {"report_md"}


def test_synthesize_ignores_diff_argument_for_markdown_output() -> None:
    """diff is kept on the signature for caller compatibility but the
    markdown output no longer changes when a diff is present (Diff section
    was dropped per ADR-096)."""
    from product_search.synthesizer import synthesize

    profile = load_profile("ddr5-rdimm-256gb")
    listing = _listing("https://x/a", total_for_target_usd=960.0)

    diff = DiffResult(
        new=[_listing("https://x/new", total_for_target_usd=500.0)],
        dropped=[_listing("https://x/old", total_for_target_usd=900.0)],
        changed=[],
    )
    with_diff = synthesize([listing], diff, profile).report_md
    without_diff = synthesize([listing], None, profile).report_md
    assert with_diff == without_diff


# ---------------------------------------------------------------------------
# Top-N-per-source ranking (issue #5: 2026-05-09 Bose run had 0 of 3 Amazon
# listings in the top 30 because used eBay listings filled every slot).
# ---------------------------------------------------------------------------


def _listing_for_source(
    source: str,
    url: str,
    *,
    total_for_target_usd: float,
    vendor_host: str | None = None,
    title: str | None = None,
) -> Listing:
    attrs: dict = {"capacity_gb": 32, "speed_mts": 4800, "form_factor": "RDIMM"}
    if vendor_host:
        attrs["vendor_host"] = vendor_host
    return Listing(
        source=source,
        url=url,
        title=title or f"{source} {url}",
        fetched_at=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
        brand="Samsung",
        mpn="M321",
        attrs=attrs,
        condition="new",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=total_for_target_usd / 8,
        kit_price_usd=None,
        quantity_available=10,
        seller_name="seller",
        seller_rating_pct=99.0,
        seller_feedback_count=100,
        ship_from_country="US",
        qvl_status="qvl",
        flags=[],
        total_for_target_usd=total_for_target_usd,
    )


def test_rank_listings_reserves_top_n_per_source():
    """Even when one source has many cheap listings, every distinct source
    gets at least SYNTH_RESERVED_PER_SOURCE rows in the top-N output."""
    from product_search.synthesizer.synthesizer import (
        SYNTH_RESERVED_PER_SOURCE,
        _rank_listings,
    )

    # 30 cheap eBay listings ($100..$129 totals) and 3 expensive Amazon
    # listings ($200..$220 totals). Pre-fix, the Amazon listings would never
    # appear in the top 30. Post-fix, the top 3 from Amazon are reserved.
    ebay = [
        _listing_for_source("ebay_search", f"https://e/{i}", total_for_target_usd=100.0 + i)
        for i in range(30)
    ]
    amazon = [
        _listing_for_source(
            "universal_ai_search",
            f"https://www.amazon.com/{i}",
            total_for_target_usd=200.0 + i * 10,
            vendor_host="amazon.com",
        )
        for i in range(3)
    ]

    ranked = _rank_listings(ebay + amazon, 30)
    assert len(ranked) == 30

    sources = [(lst.source, (lst.attrs or {}).get("vendor_host")) for lst in ranked]
    amazon_count = sum(1 for s, h in sources if s == "universal_ai_search" and h == "amazon.com")
    assert amazon_count >= min(SYNTH_RESERVED_PER_SOURCE, 3), (
        f"Expected ≥{SYNTH_RESERVED_PER_SOURCE} Amazon rows reserved; got {amazon_count}. "
        f"Sources in output: {sources}"
    )

    # Output is still sorted globally by total-for-target so the Rank column
    # reads cheapest-first.
    totals = [lst.total_for_target_usd for lst in ranked]
    assert totals == sorted(totals)


def test_rank_listings_distinguishes_universal_ai_vendors():
    """Two different universal_ai_search URLs (different vendor_host) get
    treated as separate sources for the per-source reservation."""
    from product_search.synthesizer.synthesizer import _rank_listings

    ebay = [
        _listing_for_source("ebay_search", f"https://e/{i}", total_for_target_usd=100.0 + i)
        for i in range(28)
    ]
    amazon = [
        _listing_for_source(
            "universal_ai_search",
            f"https://www.amazon.com/{i}",
            total_for_target_usd=300.0 + i,
            vendor_host="amazon.com",
        )
        for i in range(2)
    ]
    walmart = [
        _listing_for_source(
            "universal_ai_search",
            f"https://www.walmart.com/{i}",
            total_for_target_usd=350.0 + i,
            vendor_host="walmart.com",
        )
        for i in range(2)
    ]

    ranked = _rank_listings(ebay + amazon + walmart, 30)
    assert len(ranked) == 30
    hosts = [(lst.attrs or {}).get("vendor_host") for lst in ranked if lst.source == "universal_ai_search"]
    assert "amazon.com" in hosts and "walmart.com" in hosts, (
        f"Both Amazon and Walmart should appear in top 30; got hosts={hosts}"
    )
