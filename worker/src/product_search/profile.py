"""Pydantic model for product profile YAML files.

`Profile` is the canonical representation of a ``products/<slug>/profile.yaml``
file.  Every field maps 1-to-1 to a key in the YAML schema documented in
``products/_template/profile.yaml``.

Usage::

    from product_search.profile import load_profile

    profile = load_profile("ddr5-rdimm-256gb")   # raises ValidationError on bad data

The ``load_profile`` helper resolves the YAML file relative to the repo root
(two levels above this package's ``src/`` directory) so it works both when
called from the ``worker/`` working directory (CI) and from the repo root.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Allow-list of adapter IDs that may appear in ``sources[].id``
# (and ``sources_pending[].id``).  Add entries as new adapters land.
# ---------------------------------------------------------------------------
KNOWN_SOURCE_IDS: frozenset[str] = frozenset(
    [
        "ebay_search",
        "nemixram_storefront",
        "cloudstoragecorp_ebay",
        "memstore_ebay",
        "newegg_search",
        "serversupply_search",
        "memorynet_search",
        "theserverstore_storefront",
    ]
)

# ---------------------------------------------------------------------------
# Allow-list of filter/flag rule names.  Expand as validators land in Phase 3.
# ---------------------------------------------------------------------------
KNOWN_FILTER_RULES: frozenset[str] = frozenset(
    [
        "form_factor_in",
        "speed_mts_min",
        "ecc_required",
        "voltage_eq",
        "min_quantity_for_target",
        "in_stock",
        "single_sku_url",
        "title_excludes",
    ]
)

KNOWN_FLAG_RULES: frozenset[str] = frozenset(
    [
        "ship_from_country_in",
        "brand_in",
        "low_seller_feedback",
        "kingston_e_suffix",
        "title_mentions_other_server",
        "title_mentions",
    ]
)

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class TargetConfiguration(BaseModel):
    module_count: int = Field(gt=0)
    module_capacity_gb: int = Field(gt=0)


class Target(BaseModel):
    unit: str
    amount: int = Field(gt=0)
    configurations: list[TargetConfiguration] = Field(min_length=1)


class SpecAttrDef(BaseModel):
    type: Literal["int", "str", "float", "bool"]
    required: bool
    enum: list[str] | None = None


class FilterRule(BaseModel):
    rule: str
    # All extra keys (values, value, etc.) are allowed — stored in model_extra.
    model_config = {"extra": "allow"}

    @field_validator("rule")
    @classmethod
    def rule_must_be_known(cls, v: str) -> str:
        if v not in KNOWN_FILTER_RULES:
            raise ValueError(
                f"Unknown filter rule {v!r}. "
                f"Known rules: {sorted(KNOWN_FILTER_RULES)}"
            )
        return v


class FlagRule(BaseModel):
    rule: str
    flag: str
    # All extra keys (values, rating_pct_below, etc.) are allowed.
    model_config = {"extra": "allow"}

    @field_validator("rule")
    @classmethod
    def rule_must_be_known(cls, v: str) -> str:
        if v not in KNOWN_FLAG_RULES:
            raise ValueError(
                f"Unknown flag rule {v!r}. "
                f"Known rules: {sorted(KNOWN_FLAG_RULES)}"
            )
        return v


class Source(BaseModel):
    id: str
    model_config = {"extra": "allow"}

    @field_validator("id")
    @classmethod
    def id_must_be_known(cls, v: str) -> str:
        if v not in KNOWN_SOURCE_IDS:
            raise ValueError(
                f"Unknown source id {v!r}. "
                f"Known source IDs: {sorted(KNOWN_SOURCE_IDS)}"
            )
        return v


class PendingSource(BaseModel):
    id: str
    note: str | None = None
    model_config = {"extra": "allow"}

    @field_validator("id")
    @classmethod
    def id_must_be_known(cls, v: str) -> str:
        if v not in KNOWN_SOURCE_IDS:
            raise ValueError(
                f"Unknown pending source id {v!r}. "
                f"Known source IDs: {sorted(KNOWN_SOURCE_IDS)}"
            )
        return v


class Schedule(BaseModel):
    cron: str
    timezone: Literal["UTC"]

    @field_validator("cron")
    @classmethod
    def cron_must_be_valid(cls, v: str) -> str:
        """Validate a 5-field cron expression (minute hour dom month dow)."""
        fields = v.strip().split()
        if len(fields) != 5:
            raise ValueError(
                f"Cron expression must have exactly 5 fields, got {len(fields)}: {v!r}"
            )
        for field in fields:
            # Each cron field may contain digits, *, /, -, ,
            if not re.fullmatch(r"[\d*/,\-]+", field):
                raise ValueError(
                    f"Invalid cron field {field!r} in expression {v!r}"
                )
        return v


# ---------------------------------------------------------------------------
# Top-level Profile model
# ---------------------------------------------------------------------------


class Profile(BaseModel):
    """Validated representation of a ``products/<slug>/profile.yaml`` file."""

    slug: str
    display_name: str
    description: str

    target: Target
    spec_attrs: dict[str, SpecAttrDef] = Field(min_length=1)
    spec_filters: list[FilterRule] = Field(min_length=1)
    spec_flags: list[FlagRule] = Field(min_length=1)

    sources: list[Source] = Field(min_length=1)
    sources_pending: list[PendingSource] = Field(default_factory=list)

    qvl_file: str

    synthesis_hints: list[str] = Field(default_factory=list)

    schedule: Schedule

    @model_validator(mode="after")
    def slug_matches_qvl_path(self) -> Profile:
        """qvl_file must contain the profile's own slug."""
        if self.slug not in self.qvl_file:
            raise ValueError(
                f"qvl_file {self.qvl_file!r} does not reference slug {self.slug!r}"
            )
        return self


# ---------------------------------------------------------------------------
# QVL model
# ---------------------------------------------------------------------------


class QVLEntry(BaseModel):
    mpn: str
    brand: str
    capacity_gb: int = Field(gt=0)
    speed_mts: int = Field(gt=0)
    rank: str | None = None
    note: str | None = None


class QVL(BaseModel):
    qvl: list[QVLEntry]


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------

_REPO_ROOT_CANDIDATES = [
    # Running from worker/ (the normal case in CI: `working-directory: worker`)
    Path(__file__).parent.parent.parent.parent.parent,
    # Running from repo root
    Path(__file__).parent.parent.parent.parent.parent.parent,
]


def _repo_root() -> Path:
    """Return the repo root directory, tolerating both CWD setups."""
    # Prefer a path that actually contains a ``products/`` directory.
    for candidate in _REPO_ROOT_CANDIDATES:
        if (candidate / "products").is_dir():
            return candidate
    # Fallback: use cwd-relative resolution so the caller can still override.
    return Path.cwd()


def _resolve_profile_path(slug: str) -> Path:
    """Return the absolute path to ``products/<slug>/profile.yaml``."""
    repo_root = _repo_root()
    direct = repo_root / "products" / slug / "profile.yaml"
    if direct.exists():
        return direct
    # If called from worker/ (cwd), try one level up.
    parent = Path.cwd().parent / "products" / slug / "profile.yaml"
    if parent.exists():
        return parent
    raise FileNotFoundError(
        f"Profile not found for slug {slug!r}. Tried:\n"
        f"  {direct}\n"
        f"  {parent}"
    )


def load_profile(slug: str) -> Profile:
    """Load and validate the profile for *slug*.

    Raises:
        FileNotFoundError: if the YAML file does not exist.
        pydantic.ValidationError: if the YAML does not match the schema.
    """
    path = _resolve_profile_path(slug)
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Profile.model_validate(raw)


def load_qvl(slug: str) -> QVL:
    """Load and validate the QVL file for *slug*.

    Raises:
        FileNotFoundError: if the YAML file does not exist.
        pydantic.ValidationError: if the YAML does not match the schema.
    """
    repo_root = _repo_root()
    path = repo_root / "products" / slug / "qvl.yaml"
    if not path.exists():
        path = Path.cwd().parent / "products" / slug / "qvl.yaml"
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    return QVL.model_validate(raw)
