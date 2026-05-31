"""v2 -> v1 filter-profile shim (Phase 32, ADR)."""

from __future__ import annotations

from pathlib import Path

from product_search.profile_v2 import ProfileV2, load_profile_v2_from_path
from product_search.profile_v2_filter import (
    build_filter_description,
    build_spec_filters,
    to_filter_profile,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "profiles_v2"
    / "dji-neo-2-motion-fly-more-combo"
    / "profile.yaml"
)


def _fixture() -> ProfileV2:
    return load_profile_v2_from_path(FIXTURE)


def test_spec_filters_mapping() -> None:
    rules = {r.rule for r in build_spec_filters(_fixture())}
    assert "single_sku_url" in rules  # so the Serper offer-link exception runs
    assert "condition_in" in rules
    assert "in_stock" in rules
    assert "title_excludes" in rules


def test_condition_and_excludes_values() -> None:
    rules = {r.rule: r for r in build_spec_filters(_fixture())}
    assert (rules["condition_in"].model_extra or {})["values"] == ["new"]
    excl = (rules["title_excludes"].model_extra or {})["values"]
    assert "Refurbished" in excl and "Used" in excl


def test_aliases_folded_into_description() -> None:
    desc = build_filter_description(_fixture())
    assert "Neo 2 Motion Fly More" in desc
    # The distinctive aliases are surfaced for the relevance LLM.
    assert "any of these" in desc.lower()


def test_to_filter_profile_carries_core_fields() -> None:
    p = to_filter_profile(_fixture())
    assert p.slug == "dji-neo-2-motion-fly-more-combo"
    assert p.display_name
    assert p.target.amount == 1
    assert p.spec_filters


def test_min_quantity_not_mapped() -> None:
    # min_quantity is honored only for eBay (Phase 33); Serper-only Phase 32
    # must NOT add a quantity rule (it would be unhonorable on Serper data).
    raw = {
        "schema_version": 2,
        "slug": "x-prod",
        "display_name": "X Prod",
        "target": {"unit": "count", "amount": 1},
        "queries": ["x prod"],
        "filters": {"min_quantity": 10},
    }
    p2 = ProfileV2.model_validate(raw)
    rules = {r.rule for r in build_spec_filters(p2)}
    assert not any("quantity" in r for r in rules)
