"""Tests for the JSON sidecar + flag-labels registry (ADR-096).

The React post-run UI consumes ``reports/<slug>/<date>.json`` produced
by :mod:`product_search.synthesizer.report_json`. These tests pin the
shape of that payload, the flag→badge enrichment from
``flag_labels.yaml``, and the source-status derivation that replaces
the legacy ``status: ok`` everywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from product_search.models import Listing
from product_search.profile import FlagRule, Profile
from product_search.synthesizer.flag_labels import (
    flag_to_badge,
    flags_to_badges,
)
from product_search.synthesizer.report_json import (
    JSON_SCHEMA_VERSION,
    build_json_payload,
    default_json_path,
)
from tests.conftest import load_ddr5_profile as load_profile


def _listing(
    url: str = "https://www.ebay.com/itm/123",
    *,
    title: str = "Test DDR5 RDIMM 32GB",
    unit_price_usd: float = 100.0,
    total_for_target_usd: float | None = 800.0,
    flags: list[str] | None = None,
    seller_name: str | None = "some_seller",
) -> Listing:
    return Listing(
        source="ebay_search",
        url=url,
        title=title,
        fetched_at=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
        brand="Samsung",
        mpn="M321R4GA0BB0-CQK",
        attrs={"capacity_gb": 32, "speed_mts": 4800, "form_factor": "RDIMM"},
        condition="new",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=unit_price_usd,
        kit_price_usd=None,
        quantity_available=10,
        seller_name=seller_name,
        seller_rating_pct=99.5,
        seller_feedback_count=5000,
        ship_from_country="US",
        qvl_status="qvl",
        flags=flags or [],
        total_for_target_usd=total_for_target_usd,
    )


# ---------------------------------------------------------------------------
# flag_labels.yaml — three-tier lookup mirroring ADR-095's build_flags_md
# ---------------------------------------------------------------------------


def test_flag_to_badge_hits_flag_label_directly() -> None:
    """low_feedback is registered by its FLAG LABEL — direct hit, no
    fallback walk needed."""
    profile = load_profile()
    badge = flag_to_badge("low_feedback", profile)
    assert badge["key"] == "low_feedback"
    assert badge["label"] == "Limited reviews"
    assert badge["severity"] == "info"


def test_flag_to_badge_falls_back_via_rule_name() -> None:
    """A flag the registry doesn't know directly resolves via its
    originating spec_flags rule name (ADR-095 lookup pattern)."""
    base = load_profile()
    rule = FlagRule(rule="low_seller_feedback", flag="custom_label")
    profile = base.model_copy(update={"spec_flags": [rule]})
    assert isinstance(profile, Profile)
    badge = flag_to_badge("custom_label", profile)
    # The key is the FLAG label the listing carries — the badge tracks
    # which flag fired, not which rule resolved it.
    assert badge["key"] == "custom_label"
    # The label resolves via the rule-name fallback.
    assert badge["label"] == "Limited reviews"
    assert badge["severity"] == "info"


def test_flag_to_badge_raw_key_when_unmapped() -> None:
    """An unmapped flag surfaces its raw ID as the label, severity=info.
    The intent (ADR-096) is to make the gap visible — silent jargon is
    the worst case, not loud jargon."""
    profile = load_profile()
    badge = flag_to_badge("totally_made_up_flag", profile)
    assert badge["key"] == "totally_made_up_flag"
    assert badge["label"] == "totally_made_up_flag"
    assert badge["severity"] == "info"


def test_flags_to_badges_dedupes_and_preserves_order() -> None:
    profile = load_profile()
    badges = flags_to_badges(
        ["low_feedback", "china_shipping", "low_feedback"], profile
    )
    keys = [b["key"] for b in badges]
    assert keys == ["low_feedback", "china_shipping"]
    assert badges[0]["label"] == "Limited reviews"
    assert badges[1]["label"] == "Ships from China/HK"
    assert badges[1]["severity"] == "warning"


# ---------------------------------------------------------------------------
# build_json_payload — top-level shape + listings
# ---------------------------------------------------------------------------


def test_payload_carries_schema_version_and_product_metadata() -> None:
    profile = load_profile()
    payload = build_json_payload(
        listings=[_listing()],
        profile=profile,
        source_stats=[],
        run_calls=[],
        snapshot_date=None,
    )
    assert payload["schema_version"] == JSON_SCHEMA_VERSION
    assert payload["product"]["slug"] == profile.slug
    assert payload["product"]["display_name"] == profile.display_name
    assert "generated_at" in payload


def test_payload_listings_carry_per_listing_badges() -> None:
    profile = load_profile()
    listing = _listing(flags=["low_feedback", "china_shipping"])
    payload = build_json_payload(
        listings=[listing],
        profile=profile,
        source_stats=[],
        run_calls=[],
    )
    assert len(payload["listings"]) == 1
    badges = payload["listings"][0]["badges"]
    keys = [b["key"] for b in badges]
    assert keys == ["low_feedback", "china_shipping"]
    assert badges[0]["label"] == "Limited reviews"


def test_payload_listings_are_price_ranked_cheapest_first() -> None:
    """Cards must render cheapest-first since the UI no longer has a
    'winner' card — the user picks from the price-ranked stack."""
    profile = load_profile()
    listings = [
        _listing("https://x/a", total_for_target_usd=900.0),
        _listing("https://x/b", total_for_target_usd=500.0),
        _listing("https://x/c", total_for_target_usd=700.0),
    ]
    payload = build_json_payload(
        listings=listings, profile=profile, source_stats=[], run_calls=[]
    )
    urls = [row["url"] for row in payload["listings"]]
    ranks = [row["rank"] for row in payload["listings"]]
    assert urls == ["https://x/b", "https://x/c", "https://x/a"]
    assert ranks == [1, 2, 3]


def test_payload_listings_carry_total_and_price_independently() -> None:
    profile = load_profile()
    listing = _listing(unit_price_usd=120.0, total_for_target_usd=960.0)
    payload = build_json_payload(
        listings=[listing], profile=profile, source_stats=[], run_calls=[]
    )
    row = payload["listings"][0]
    assert row["price_usd"] == pytest.approx(120.0)
    assert row["total_for_target_usd"] == pytest.approx(960.0)


def test_payload_handles_zero_listings_gracefully() -> None:
    profile = load_profile()
    payload = build_json_payload(
        listings=[], profile=profile, source_stats=[], run_calls=[]
    )
    assert payload["listings"] == []
    assert payload["listings_meta"] == {
        "total_passed": 0,
        "shown": 0,
        "cap": 30,
    }


# ---------------------------------------------------------------------------
# Source status derivation (ADR-084 classifier, surfaced in JSON)
# ---------------------------------------------------------------------------


def test_source_status_is_ok_when_passed_listings_present() -> None:
    profile = load_profile()
    payload = build_json_payload(
        listings=[],
        profile=profile,
        source_stats=[{
            "source": "ebay_search",
            "display_source": "ebay_search",
            "fetched": 5,
            "passed": 3,
            "error": None,
        }],
        run_calls=[],
    )
    src = payload["sources"][0]
    assert src["status"] == "ok"


def test_source_status_is_no_match_when_fetched_but_zero_passed() -> None:
    """The user's specific complaint: fetched=1 passed=0 must NOT show ok."""
    profile = load_profile()
    payload = build_json_payload(
        listings=[],
        profile=profile,
        source_stats=[{
            "source": "universal_ai_search",
            "display_source": "discountmags.com",
            "fetched": 1,
            "passed": 0,
            "error": None,
        }],
        run_calls=[],
    )
    src = payload["sources"][0]
    assert src["status"] == "no_match"
    assert src["status_label"] == "no match"
    assert "search criteria" in src["reason"].lower()


def test_source_status_is_transient_when_alterlab_degraded() -> None:
    profile = load_profile()
    payload = build_json_payload(
        listings=[],
        profile=profile,
        source_stats=[{
            "source": "universal_ai_search",
            "display_source": "magsstore.com",
            "fetched": 0,
            "passed": 0,
            "error": None,
            "diagnostics": {"alterlab_degraded": True},
        }],
        run_calls=[],
    )
    src = payload["sources"][0]
    assert src["status"] == "transient"
    assert src["status_label"] == "transient"


def test_source_status_is_empty_page_when_no_error_and_short_body() -> None:
    """No error, fetched=0, body too thin to be a real product page →
    EMPTY_PAGE (genuine no-results), not OK."""
    profile = load_profile()
    payload = build_json_payload(
        listings=[],
        profile=profile,
        source_stats=[{
            "source": "universal_ai_search",
            "display_source": "magazineline.com",
            "fetched": 0,
            "passed": 0,
            "error": None,
            "diagnostics": {"body_len": 1234},
        }],
        run_calls=[],
    )
    src = payload["sources"][0]
    assert src["status"] == "empty_page"
    assert src["status_label"] == "no results"


# ---------------------------------------------------------------------------
# Run-cost shape
# ---------------------------------------------------------------------------


def test_run_cost_payload_has_no_synth_row_post_adr096() -> None:
    """ADR-096 retires the synth LLM call. Callers no longer append a
    `synth` row — the payload should reflect exactly what cli.py builds."""
    profile = load_profile()
    payload = build_json_payload(
        listings=[],
        profile=profile,
        source_stats=[],
        run_calls=[
            {
                "step": "ai_filter",
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
                "input_tokens": 1000,
                "output_tokens": 200,
            },
        ],
    )
    steps = payload["run_cost"]["steps"]
    assert [s["step"] for s in steps] == ["ai_filter"]
    assert payload["run_cost"]["total_usd"] >= 0.0


# ---------------------------------------------------------------------------
# Sidecar path
# ---------------------------------------------------------------------------


def test_default_json_path_sits_next_to_markdown() -> None:
    from datetime import date

    p = default_json_path("the-week-1yr-subscription", date(2026, 5, 26))
    assert p.name == "2026-05-26.json"
    assert p.parent.name == "the-week-1yr-subscription"
