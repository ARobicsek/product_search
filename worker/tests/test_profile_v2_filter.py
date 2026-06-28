"""v2 -> v1 filter-profile shim (Phase 32, ADR)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_search.models import Listing
from product_search.profile_v2 import ProfileV2, load_profile_v2_from_path
from product_search.profile_v2_filter import (
    build_filter_description,
    build_spec_filters,
    partition_by_exact_alias,
    title_has_exact_alias,
    title_states_excluded_condition,
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


# ---------------------------------------------------------------------------
# Deterministic alias-match pre-pass (Phase 41 / ADR-145)
# ---------------------------------------------------------------------------


def _make_listing(title: str, **overrides: Any) -> Listing:
    defaults: dict[str, Any] = {
        "source": "serper_shopping",
        "url": "https://www.google.com/search?ibp=oshop&q=anything",
        "title": title,
        "fetched_at": datetime.now(tz=UTC),
        "brand": None,
        "mpn": None,
        "attrs": {},
        "condition": "",
        "is_kit": False,
        "kit_module_count": 1,
        "unit_price_usd": 100.0,
        "kit_price_usd": None,
        "quantity_available": None,
        "seller_name": "test_seller",
        "seller_rating_pct": None,
        "seller_feedback_count": None,
        "ship_from_country": None,
    }
    defaults.update(overrides)
    return Listing(**defaults)


def _v2(aliases: list[str], condition_in: list[str] | None = None) -> ProfileV2:
    raw: dict[str, Any] = {
        "schema_version": 2,
        "slug": "x-prod",
        "display_name": "X Prod",
        "target": {"unit": "count", "amount": 1},
        "queries": ["x prod"],
        "match": {"aliases": aliases},
    }
    if condition_in is not None:
        raw["filters"] = {"condition_in": condition_in}
    return ProfileV2.model_validate(raw)


def test_title_has_exact_alias_basic() -> None:
    aliases = ["HMCG84AGBRA191N", "Neo 2 Motion Fly More"]
    assert title_has_exact_alias("Hynix HMCG84AGBRA191N 32GB ECC", aliases)
    # Case-insensitive + whitespace-collapsed multi-word.
    assert title_has_exact_alias("DJI  Neo 2  Motion  Fly More Combo", aliases)
    # A sibling SKU must NOT match (only the listed exact string counts).
    assert not title_has_exact_alias("Hynix HMCG84AGBRA190N 32GB ECC", aliases)
    assert not title_has_exact_alias("Unrelated DDR5 32GB", aliases)


def test_title_states_excluded_condition() -> None:
    # Empty condition_in = allow all → never excluded.
    assert not title_states_excluded_condition("Used HMCG84AGBRA191N", [])
    # condition_in=["new"]: a stated used/refurbished/open-box is excluded.
    assert title_states_excluded_condition("HMCG84AGBRA191N Refurbished", ["new"])
    assert title_states_excluded_condition("Pre-Owned HMCG84AGBRA191N", ["new"])
    assert title_states_excluded_condition("HMCG84AGBRA191N (Open Box)", ["new"])
    # "unused" must NOT trip the \\bused\\b cue.
    assert not title_states_excluded_condition("New Unused HMCG84AGBRA191N", ["new"])
    # A stated condition that IS allowed → not excluded.
    assert not title_states_excluded_condition("Refurbished HMCG84AGBRA191N", ["refurbished"])


def test_partition_auto_passes_exact_alias_regardless_of_condition_when_allowed() -> None:
    # The real RAM case: condition_in=[] (allow all). The exact part stated as
    # "Refurbished" must auto-pass (the LLM previously fabricated a rejection).
    hit = _make_listing("Hynix HMCG84AGBRA191N 32GB DDR5-5600 ECC Refurbished")
    junk = _make_listing("Dell 32GB DDR5 Server Memory")
    hits, remainder = partition_by_exact_alias([hit, junk], _v2(["HMCG84AGBRA191N"], condition_in=[]))
    assert hits == [hit]
    assert remainder == [junk]


def test_partition_routes_excluded_condition_alias_to_remainder() -> None:
    # With an ACTIVE condition_in=["new"], a "Used" exact-alias listing must NOT
    # be auto-passed — it goes to the LLM remainder instead.
    used = _make_listing("Hynix HMCG84AGBRA191N 32GB ECC Used")
    new_hit = _make_listing("Hynix HMCG84AGBRA191N 32GB ECC")
    hits, remainder = partition_by_exact_alias([used, new_hit], _v2(["HMCG84AGBRA191N"], condition_in=["new"]))
    assert hits == [new_hit]
    assert remainder == [used]


def test_partition_no_aliases_is_noop() -> None:
    a = _make_listing("anything")
    hits, remainder = partition_by_exact_alias([a], _v2([]))
    assert hits == []
    assert remainder == [a]
