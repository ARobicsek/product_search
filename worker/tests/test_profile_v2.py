"""Tests for the v2 profile schema + loader (Phase 31, ADR-133/134)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from product_search.profile_v2 import ProfileV2, load_profile_v2_from_path

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "profiles_v2"
    / "dji-neo-2-motion-fly-more-combo"
    / "profile.yaml"
)


def _minimal() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "slug": "x",
        "display_name": "X",
        "target": {"unit": "unit", "amount": 1},
        "queries": ["x query"],
    }


def test_loads_fixture_profile_all_blocks() -> None:
    p = load_profile_v2_from_path(_FIXTURE)
    assert p.schema_version == 2
    assert p.slug == "dji-neo-2-motion-fly-more-combo"
    assert p.product_type == "drone"
    assert p.queries == ["DJI Neo 2 Motion Fly More Combo"]
    # match block
    assert p.match.variant_strict is True
    assert "DJI Neo 2" in p.match.aliases
    assert "Refurbished" in p.match.title_excludes
    # filters block
    assert p.filters.condition_in == ["new"]
    assert p.filters.in_stock is True
    # sources block
    assert p.sources.serper.enabled is True
    assert p.sources.serper.gl == "us"
    assert p.sources.ebay.enabled is True
    # display block
    assert p.display.max_listings == 20
    assert p.display.per_vendor_cap == 3
    assert "price" in p.display.attrs
    # flags stay permissive (open dicts)
    assert p.flags and isinstance(p.flags[0], dict)


def test_minimal_profile_uses_defaults() -> None:
    p = ProfileV2.model_validate(_minimal())
    # Defaulted sub-models.
    assert p.match.variant_strict is True  # global default strict (§11 decision 1)
    assert p.sources.serper.enabled is True
    assert p.sources.ebay.enabled is False  # off by default (§11 decision 2)
    assert p.display.max_listings == 20
    assert p.filters.condition_in is None
    assert p.description == ""


def test_wrong_schema_version_rejected() -> None:
    bad = _minimal()
    bad["schema_version"] = 1
    with pytest.raises(ValidationError):
        ProfileV2.model_validate(bad)


def test_missing_schema_version_rejected() -> None:
    bad = _minimal()
    del bad["schema_version"]
    with pytest.raises(ValidationError):
        ProfileV2.model_validate(bad)


def test_empty_queries_rejected() -> None:
    bad = _minimal()
    bad["queries"] = []
    with pytest.raises(ValidationError):
        ProfileV2.model_validate(bad)


def test_blank_query_string_rejected() -> None:
    bad = _minimal()
    bad["queries"] = ["  "]
    with pytest.raises(ValidationError):
        ProfileV2.model_validate(bad)


def test_non_distinctive_alias_rejected() -> None:
    bad = _minimal()
    bad["match"] = {"aliases": ["Drone"]}  # single generic word, no digit
    with pytest.raises(ValidationError):
        ProfileV2.model_validate(bad)


def test_distinctive_alias_accepted() -> None:
    ok = _minimal()
    ok["match"] = {"aliases": ["Neo 2", "CP.FP.00000273.01"]}
    p = ProfileV2.model_validate(ok)
    assert p.match.aliases == ["Neo 2", "CP.FP.00000273.01"]


def test_v1_yaml_shape_fails_v2() -> None:
    """A v1-shaped profile (list[Source] sources, no schema_version) must not
    validate as v2 — the discriminator + queries requirement guard this."""
    v1_shaped = {
        "slug": "x",
        "display_name": "X",
        "target": {"unit": "unit", "amount": 1},
        "sources": [{"id": "ebay_search", "queries": ["x"]}],
    }
    with pytest.raises(ValidationError):
        ProfileV2.model_validate(v1_shaped)


def test_display_caps_must_be_positive() -> None:
    bad = _minimal()
    bad["display"] = {"max_listings": 0}
    with pytest.raises(ValidationError):
        ProfileV2.model_validate(bad)


def test_fixture_is_not_mutated_across_loads() -> None:
    # Sanity: loading twice yields equivalent parsed models.
    a = load_profile_v2_from_path(_FIXTURE)
    b = load_profile_v2_from_path(_FIXTURE)
    assert a.model_dump() == b.model_dump()
    # And the helper above didn't mutate our minimal dict template.
    assert _minimal() == copy.deepcopy(_minimal())
