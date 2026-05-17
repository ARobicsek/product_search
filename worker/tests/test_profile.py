"""Phase 1 tests — Profile schema validation.

Coverage:
  1. Happy path: the real DDR5 profile + QVL passes.
  2. Reject: missing required field (``slug``).
  3. Reject: unknown source ID.
  4. Reject: invalid cron expression.
  5. Reject: unknown filter rule.
  6. CLI integration: ``product-search validate ddr5-rdimm-256gb`` exits 0.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from product_search.profile import Profile, Schedule, load_profile, load_qvl

# ---------------------------------------------------------------------------
# Locate the repo root so tests can build minimal profile fixtures.
# We look for the ``products/`` directory starting from the test file's
# location and walking upward.
# ---------------------------------------------------------------------------


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "products").is_dir():
            return parent
    raise RuntimeError("Could not find repo root (no products/ dir found in parents)")


REPO_ROOT = _find_repo_root()
DDR5_SLUG = "ddr5-rdimm-256gb"

# ---------------------------------------------------------------------------
# Minimal valid profile dict (mirrors _template/profile.yaml structure)
# ---------------------------------------------------------------------------

VALID_PROFILE: dict[str, Any] = {
    "slug": "test-product",
    "display_name": "Test Product",
    "description": "A product used only in tests.",
    "target": {
        "unit": "GB",
        "amount": 256,
        "configurations": [
            {"module_count": 8, "module_capacity_gb": 32},
        ],
    },
    "spec_attrs": {
        "capacity_gb": {"type": "int", "required": True},
        "speed_mts": {"type": "int", "required": True},
        "form_factor": {
            "type": "str",
            "required": True,
            "enum": ["RDIMM", "3DS-RDIMM", "UDIMM", "SODIMM", "LRDIMM"],
        },
        "ecc": {"type": "bool", "required": True},
    },
    "spec_filters": [
        {"rule": "form_factor_in", "values": ["RDIMM", "3DS-RDIMM"]},
        {"rule": "ecc_required"},
        {"rule": "in_stock"},
    ],
    "spec_flags": [
        {"rule": "brand_in", "values": ["HPE"], "flag": "smart_memory"},
    ],
    "sources": [
        {"id": "ebay_search", "queries": ["DDR5 ECC 32GB"], "max_results_per_query": 50},
    ],
    "sources_pending": [],
    "qvl_file": "products/test-product/qvl.yaml",
    "synthesis_hints": [],
    "schedule": {
        "cron": "0 8 * * *",
        "timezone": "UTC",
    },
}

# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_real_ddr5_profile_valid() -> None:
    """The committed DDR5 profile must pass schema validation."""
    profile = load_profile(DDR5_SLUG)
    assert profile.slug == DDR5_SLUG
    assert profile.target.amount == 256
    assert len(profile.sources) >= 1


def test_real_ddr5_qvl_valid() -> None:
    """The committed DDR5 QVL must pass schema validation."""
    qvl = load_qvl(DDR5_SLUG)
    assert len(qvl.qvl) >= 1
    # Every entry must have a non-empty MPN and brand.
    for entry in qvl.qvl:
        assert entry.mpn
        assert entry.brand


def test_minimal_valid_profile_passes() -> None:
    """A hand-crafted minimal dict must validate without errors."""
    profile = Profile.model_validate(VALID_PROFILE)
    assert profile.slug == "test-product"


# ---------------------------------------------------------------------------
# Rejection tests
# ---------------------------------------------------------------------------


def test_rejects_missing_required_field() -> None:
    """A profile missing ``slug`` must raise ValidationError."""
    bad = {k: v for k, v in VALID_PROFILE.items() if k != "slug"}
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    errors = exc_info.value.errors()
    field_names = [e["loc"][0] for e in errors]
    assert "slug" in field_names


def test_rejects_unknown_source_id() -> None:
    """A profile referencing an unknown source ID must raise ValidationError."""
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["sources"] = [{"id": "totally_unknown_source", "queries": ["DDR5"]}]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    # The error message should mention the unknown id.
    assert "totally_unknown_source" in str(exc_info.value)


def test_accepts_universal_ai_search_source() -> None:
    """The universal_ai_search adapter (ADR-029) is wired in cli.py and
    must validate as a known source. Pinned because the schema has been
    out-of-sync with cli.py before — the onboarding UI surfaced
    'unknown source id "universal_ai_search"' when the AI emitted it."""
    import copy

    p = copy.deepcopy(VALID_PROFILE)
    p["sources"] = [
        {"id": "universal_ai_search", "url": "https://example.com/collections/headphones"},
    ]
    Profile.model_validate(p)  # must not raise


def test_rejects_invalid_cron() -> None:
    """A profile with a malformed cron expression must raise ValidationError."""
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["schedule"] = {"cron": "not-a-cron", "timezone": "UTC"}
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "cron" in str(exc_info.value).lower() or "field" in str(exc_info.value).lower()


def test_schedule_recurring_cron_only_valid() -> None:
    """A cron-only schedule is valid; timezone defaults to UTC when omitted."""
    s = Schedule.model_validate({"cron": "30 13 * * *"})
    assert s.cron == "30 13 * * *"
    assert s.run_at is None
    assert s.timezone == "UTC"


def test_schedule_one_time_run_at_normalised_to_utc() -> None:
    """A run_at-only schedule is valid; the instant is normalised to UTC."""
    s = Schedule.model_validate({"run_at": "2026-05-17T08:30:00-04:00"})
    assert s.cron is None
    assert s.run_at is not None
    assert s.run_at.tzinfo is not None
    # 08:30 EDT == 12:30 UTC.
    assert s.run_at.hour == 12 and s.run_at.minute == 30

    # A naive timestamp is interpreted as UTC.
    naive = Schedule.model_validate({"run_at": "2026-05-17T12:30:00"})
    assert naive.run_at is not None and naive.run_at.hour == 12
    assert naive.run_at.tzinfo is not None


def test_schedule_rejects_both_cron_and_run_at() -> None:
    with pytest.raises(ValidationError) as exc:
        Schedule.model_validate(
            {"cron": "0 8 * * *", "run_at": "2026-05-17T12:30:00Z"}
        )
    assert "not both" in str(exc.value)


def test_schedule_rejects_neither_cron_nor_run_at() -> None:
    with pytest.raises(ValidationError) as exc:
        Schedule.model_validate({"timezone": "UTC"})
    assert "one of" in str(exc.value)


def test_profile_accepts_one_time_schedule() -> None:
    """Profile round-trips a one-time schedule (web save path relies on this)."""
    import copy

    p = copy.deepcopy(VALID_PROFILE)
    p["schedule"] = {"run_at": "2026-05-17T12:30:00Z", "timezone": "UTC"}
    profile = Profile.model_validate(p)
    assert profile.schedule is not None
    assert profile.schedule.cron is None
    assert profile.schedule.run_at is not None


def test_rejects_unknown_filter_rule() -> None:
    """A spec_filters entry with an unknown rule must raise ValidationError."""
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["spec_filters"] = [{"rule": "nonexistent_filter_rule"}]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "nonexistent_filter_rule" in str(exc_info.value)


def test_report_columns_optional_defaults_none() -> None:
    """report_columns is optional; absent → None (synthesizer falls back)."""
    profile = Profile.model_validate(VALID_PROFILE)
    assert profile.report_columns is None


def test_report_columns_accepts_known_ids() -> None:
    import copy

    good = copy.deepcopy(VALID_PROFILE)
    good["report_columns"] = ["rank", "title", "condition", "price_unit", "seller"]
    profile = Profile.model_validate(good)
    assert profile.report_columns == ["rank", "title", "condition", "price_unit", "seller"]


def test_rejects_unknown_report_column() -> None:
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["report_columns"] = ["rank", "totally_unknown_column"]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "totally_unknown_column" in str(exc_info.value)


def test_rejects_duplicate_report_columns() -> None:
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["report_columns"] = ["rank", "title", "rank"]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "duplicate" in str(exc_info.value).lower()


def test_rejects_empty_report_columns() -> None:
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["report_columns"] = []
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "non-empty" in str(exc_info.value).lower()


def test_brand_candidates_optional_defaults_none() -> None:
    profile = Profile.model_validate(VALID_PROFILE)
    assert profile.brand_candidates is None


def test_brand_candidates_accepts_strings() -> None:
    import copy

    good = copy.deepcopy(VALID_PROFILE)
    good["brand_candidates"] = ["Bose", "Sony"]
    profile = Profile.model_validate(good)
    assert profile.brand_candidates == ["Bose", "Sony"]


def test_rejects_empty_brand_candidates_list() -> None:
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["brand_candidates"] = []
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "non-empty" in str(exc_info.value).lower()


def test_rejects_blank_brand_candidate() -> None:
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["brand_candidates"] = ["Bose", "   "]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "non-empty" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Non-RAM optional fields (target.configurations + qvl_file)
# ---------------------------------------------------------------------------


def test_target_configurations_is_optional_for_non_ram() -> None:
    """Non-RAM profiles can omit `target.configurations` entirely.

    Pinned because the onboarder used to emit a degenerate
    [{module_count: 1, module_capacity_gb: 1}] placeholder for
    single-unit consumer goods (paintball pistols, headphones, etc.).
    The placeholder is meaningless and noisy — the schema now allows
    the list to be empty (or omitted, defaulting to []).
    """
    import copy

    p = copy.deepcopy(VALID_PROFILE)
    p["target"] = {"unit": "count", "amount": 1}  # No `configurations` key.
    p["spec_attrs"] = {}
    p["spec_filters"] = [{"rule": "in_stock"}]
    profile = Profile.model_validate(p)
    assert profile.target.configurations == []


def test_qvl_file_is_optional_for_non_ram() -> None:
    """Non-RAM profiles can omit `qvl_file` entirely.

    Pinned because QVL is RAM-specific (manufacturer-published DIMM
    compatibility lists). A paintball pistol or pair of headphones has
    no analogous reference data, so emitting a path to an empty
    qvl.yaml is pure noise.
    """
    import copy

    p = copy.deepcopy(VALID_PROFILE)
    del p["qvl_file"]
    profile = Profile.model_validate(p)
    assert profile.qvl_file is None


def test_qvl_file_when_set_must_still_contain_slug() -> None:
    """Optional doesn't mean unchecked: a stale qvl_file path still rejects."""
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["qvl_file"] = "products/some-other-product/qvl.yaml"
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "test-product" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Alerts (Phase 17)
# ---------------------------------------------------------------------------


def test_alerts_optional_defaults_empty() -> None:
    """A profile without an ``alerts`` key must default to []."""
    profile = Profile.model_validate(VALID_PROFILE)
    assert profile.alerts == []


def test_accepts_price_below_alert_minimal() -> None:
    import copy

    good = copy.deepcopy(VALID_PROFILE)
    good["alerts"] = [{"kind": "price_below", "threshold_usd": 199.99}]
    profile = Profile.model_validate(good)
    assert len(profile.alerts) == 1
    rule = profile.alerts[0]
    assert rule.kind == "price_below"
    assert rule.threshold_usd == 199.99
    assert rule.condition is None


def test_accepts_price_below_alert_with_condition() -> None:
    import copy

    good = copy.deepcopy(VALID_PROFILE)
    good["alerts"] = [{"kind": "price_below", "threshold_usd": 150.0, "condition": "new"}]
    profile = Profile.model_validate(good)
    assert profile.alerts[0].condition == "new"


def test_accepts_vendor_seen_alert() -> None:
    import copy

    good = copy.deepcopy(VALID_PROFILE)
    good["alerts"] = [{"kind": "vendor_seen", "host": "amazon.com"}]
    profile = Profile.model_validate(good)
    assert profile.alerts[0].kind == "vendor_seen"
    assert profile.alerts[0].host == "amazon.com"


def test_rejects_unknown_alert_kind() -> None:
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["alerts"] = [{"kind": "totally_unknown", "host": "amazon.com"}]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "totally_unknown" in str(exc_info.value) or "kind" in str(exc_info.value).lower()


def test_rejects_price_below_with_nonpositive_threshold() -> None:
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["alerts"] = [{"kind": "price_below", "threshold_usd": 0}]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "threshold_usd" in str(exc_info.value).lower() or "greater" in str(exc_info.value).lower()


def test_rejects_invalid_alert_condition() -> None:
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["alerts"] = [{"kind": "price_below", "threshold_usd": 100, "condition": "open-box"}]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "condition" in str(exc_info.value).lower() or "open-box" in str(exc_info.value)


def test_rejects_empty_vendor_host() -> None:
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["alerts"] = [{"kind": "vendor_seen", "host": ""}]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "host" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# CLI integration test
# ---------------------------------------------------------------------------


def test_cli_validate_ddr5_exits_zero() -> None:
    """``product-search validate ddr5-rdimm-256gb`` must exit 0."""
    result = subprocess.run(
        [sys.executable, "-m", "product_search.cli", "validate", DDR5_SLUG],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "worker"),
    )
    assert result.returncode == 0, (
        f"CLI validate failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert "valid" in result.stdout.lower()
