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

import pytest
from pydantic import ValidationError

from product_search.profile import Profile, load_profile, load_qvl

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

VALID_PROFILE: dict = {
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


def test_rejects_invalid_cron() -> None:
    """A profile with a malformed cron expression must raise ValidationError."""
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["schedule"] = {"cron": "not-a-cron", "timezone": "UTC"}
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "cron" in str(exc_info.value).lower() or "field" in str(exc_info.value).lower()


def test_rejects_unknown_filter_rule() -> None:
    """A spec_filters entry with an unknown rule must raise ValidationError."""
    import copy

    bad = copy.deepcopy(VALID_PROFILE)
    bad["spec_filters"] = [{"rule": "nonexistent_filter_rule"}]
    with pytest.raises(ValidationError) as exc_info:
        Profile.model_validate(bad)
    assert "nonexistent_filter_rule" in str(exc_info.value)


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
