"""v2 -> v1 filter-profile shim (Phase 32, ADR)."""

from __future__ import annotations

from pathlib import Path

from product_search.profile_v2 import ProfileV2, load_profile_v2_from_path
from product_search.profile_v2_filter import (
    build_filter_description,
    build_spec_filters,
    distinctive_aliases,
    title_has_exact_alias,
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
# Alias matching (Phase 41 / ADR-145, redesigned ADR-150)
# ---------------------------------------------------------------------------


def test_title_has_exact_alias_basic() -> None:
    aliases = ["HMCG84AGBRA191N", "Neo 2 Motion Fly More"]
    assert title_has_exact_alias("Hynix HMCG84AGBRA191N 32GB ECC", aliases)
    # Case-insensitive + whitespace-collapsed multi-word.
    assert title_has_exact_alias("DJI  Neo 2  Motion  Fly More Combo", aliases)
    # A sibling SKU must NOT match (only the listed exact string counts).
    assert not title_has_exact_alias("Hynix HMCG84AGBRA190N 32GB ECC", aliases)
    assert not title_has_exact_alias("Unrelated DDR5 32GB", aliases)


def test_title_has_exact_alias_token_boundary() -> None:
    # ADR-150: the alias must match as a DISTINCT token. "H14SSL-N" must NOT
    # match the different SKU "H14SSL-NT", but must still match next to a hyphen
    # or space (the real product, incl. the retail-boxed "MBD-H14SSL-N-O" form).
    aliases = ["H14SSL-N"]
    assert not title_has_exact_alias("Supermicro H14SSL-NT AMD EPYC Motherboard", aliases)
    assert title_has_exact_alias("Supermicro H14SSL-N SP5 Server Motherboard", aliases)
    assert title_has_exact_alias("Supermicro Motherboard (MBD-H14SSL-N-O)", aliases)
    # A compatible-part title genuinely contains the token; the matcher is honest
    # about that (the LLM, not this matcher, judges it an accessory).
    assert title_has_exact_alias("32GB ECC Memory for Supermicro H14SSL-N Server", aliases)


def test_distinctive_aliases_strips_and_keeps_nonempty() -> None:
    out = distinctive_aliases(_fixture())
    assert out  # the DJI fixture carries aliases
    assert any("Neo 2" in a for a in out)
    assert all(a == a.strip() for a in out)


def test_to_filter_profile_carries_match_aliases() -> None:
    # The distinctive aliases reach the filter Profile so ai_filter can attach
    # the per-listing model-name signal (ADR-150).
    p = to_filter_profile(_fixture())
    assert p.match_aliases == distinctive_aliases(_fixture())
    assert any("Neo 2" in a for a in p.match_aliases)
