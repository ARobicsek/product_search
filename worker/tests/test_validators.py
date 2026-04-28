"""Tests for the Phase 3 validator pipeline."""

from __future__ import annotations

from product_search.profile import QVL, FilterRule, FlagRule, Profile
from product_search.validators.filters import apply_filters
from product_search.validators.flags import apply_flags
from product_search.validators.pipeline import run_pipeline
from product_search.validators.qvl import annotate_qvl
from tests.test_phase2 import _make_listing
from tests.test_profile import VALID_PROFILE


def _make_profile() -> Profile:
    return Profile.model_validate(VALID_PROFILE)


def test_reject_form_factor() -> None:
    lst = _make_listing(attrs={"form_factor": "UDIMM"})
    profile = _make_profile()
    rules = [FilterRule.model_validate({"rule": "form_factor_in", "values": ["RDIMM"]})]
    reason = apply_filters(lst, rules, profile)
    assert reason is not None
    assert "form_factor" in reason
    assert "UDIMM" in reason

    lst.attrs["form_factor"] = "RDIMM"
    assert apply_filters(lst, rules, profile) is None


def test_reject_speed() -> None:
    lst = _make_listing(attrs={"speed_mts": 4000})
    profile = _make_profile()
    rules = [FilterRule.model_validate({"rule": "speed_mts_min", "value": 4800})]
    assert apply_filters(lst, rules, profile) is not None

    lst.attrs["speed_mts"] = 4800
    assert apply_filters(lst, rules, profile) is None


def test_reject_min_quantity() -> None:
    # Target needs 256GB, config says 8x32GB.
    lst = _make_listing(
        attrs={"capacity_gb": 32},
        kit_module_count=1,
        quantity_available=7,
    )
    profile = _make_profile()
    rules = [FilterRule.model_validate({"rule": "min_quantity_for_target"})]
    
    # 7 is less than 8 needed
    assert apply_filters(lst, rules, profile) is not None

    # 8 is exactly enough
    lst.quantity_available = 8
    assert apply_filters(lst, rules, profile) is None

    # Unknown quantity is allowed
    lst.quantity_available = None
    assert apply_filters(lst, rules, profile) is None

    # Unknown capacity is allowed
    lst.attrs.pop("capacity_gb")
    assert apply_filters(lst, rules, profile) is None

    # Non-matching capacity is rejected
    lst.attrs["capacity_gb"] = 16
    assert apply_filters(lst, rules, profile) is not None


def test_flag_china_shipping() -> None:
    lst = _make_listing(ship_from_country="CN")
    rules = [
        FlagRule.model_validate(
            {"rule": "ship_from_country_in", "values": ["CN", "HK"], "flag": "china_shipping"}
        )
    ]
    apply_flags(lst, rules)
    assert "china_shipping" in lst.flags

    lst.flags.clear()
    lst.ship_from_country = "US"
    apply_flags(lst, rules)
    assert "china_shipping" not in lst.flags


def test_flag_kingston_e_suffix() -> None:
    lst = _make_listing(brand="Kingston", mpn="KSM48R40BD4TMI-32HMI")
    rules = [FlagRule.model_validate({"rule": "kingston_e_suffix", "flag": "kingston_e_is_udimm"})]
    apply_flags(lst, rules)
    assert "kingston_e_is_udimm" not in lst.flags

    lst.mpn = "KSM48E40BD8KM-32HME"
    apply_flags(lst, rules)
    assert "kingston_e_is_udimm" in lst.flags


def test_annotate_qvl() -> None:
    qvl = QVL.model_validate({
        "qvl": [
            {"mpn": "EXACT-MATCH", "brand": "Samsung", "capacity_gb": 32, "speed_mts": 4800}
        ]
    })
    
    lst = _make_listing(mpn="EXACT-MATCH")
    annotate_qvl(lst, qvl)
    assert lst.qvl_status == "qvl"

    lst.mpn = "OTHER-MATCH"
    annotate_qvl(lst, qvl)
    assert lst.qvl_status == "unknown"


def test_pipeline_calculates_total_cost() -> None:
    lst = _make_listing(
        attrs={"capacity_gb": 32},
        is_kit=True,
        kit_module_count=4,
        unit_price_usd=100.0,
        kit_price_usd=380.0,
        quantity_available=10,
    )
    profile = _make_profile()
    # profile needs 8x32GB. So it needs 2 of these 4-module kits.
    # Total cost should be 2 * 380 = 760.0
    
    passed, rejected = run_pipeline([lst], profile, None)
    assert rejected == 0
    assert len(passed) == 1
    
    # 2 kits * 380
    assert passed[0].total_for_target_usd == 760.0


def test_pipeline_flags_unknown_quantity() -> None:
    lst = _make_listing(
        attrs={"capacity_gb": 32},
        quantity_available=None,
    )
    profile = _make_profile()
    passed, rejected = run_pipeline([lst], profile, None)
    assert rejected == 0
    assert "unknown_quantity" in passed[0].flags
