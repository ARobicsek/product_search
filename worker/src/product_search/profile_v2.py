"""Profile schema v2 — the Serper-recall rebuild shape (Phase 31, ADR-133/134).

Where v1 ``Profile`` (``profile.py``) is a curated list of vendor sources with
URLs / ``page_type`` / ``alterlab_options``, v2 collapses sourcing to **"query +
spec"**: the recall layer is a query sent to shopping adapters (Serper always;
eBay opt-in), so a profile no longer names vendors or URLs.

``ProfileV2`` is a **separate** Pydantic model, NOT an extension of v1
``Profile``: v2 redefines ``sources`` from a ``list[Source]`` to a typed object
(``SourcesV2``), which would clash with the v1 field. Both models coexist until
Phase 36 retires the v1 scraping pipeline. A required ``schema_version: 2``
discriminator makes a v1 YAML fail ``ProfileV2`` validation (and lets a future
unified loader sniff which model to use).

v1's ``Target`` and ``Schedule`` are reused verbatim (imported below); only the
profile-shaped parts that actually change get new sub-models here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from product_search.profile import (
    Schedule,
    Target,
    _resolve_profile_path,  # reused: same env-override + repo-root resolution
)

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class MatchSpec(BaseModel):
    """How a fetched listing is matched to the requested product.

    ``aliases`` opens the (v1) carry-gate / disambiguation; each must be
    DISTINCTIVE (a digit OR a multi-word phrase) for the same reason as v1's
    ``match_aliases`` — a bare generic word would match a vendor's whole
    catalog. ``variant_strict`` (ADR-117) controls exact-SKU (true) vs family
    breadth (false); default true per REBUILD_PLAN §11 decision 1 (real Haiku
    already leans strict — STRESS_TEST_30 Step 3b).
    """

    aliases: list[str] = Field(default_factory=list)
    title_excludes: list[str] = Field(default_factory=list)
    variant_strict: bool = True

    @field_validator("aliases")
    @classmethod
    def aliases_must_be_distinctive(cls, v: list[str]) -> list[str]:
        for a in v:
            if not isinstance(a, str) or not a.strip():
                raise ValueError("match.aliases: each entry must be a non-empty string")
            stripped = a.strip()
            has_digit = any(ch.isdigit() for ch in stripped)
            is_multiword = len(stripped.split()) >= 2
            if not (has_digit or is_multiword):
                raise ValueError(
                    f"match.aliases entry {a!r} is too generic — a single word with "
                    f"no digit would match a vendor's whole catalog and defeat the "
                    f"carry-gate (ADR-099). Use the model number, a SKU form, or a "
                    f"multi-word marketing phrase."
                )
        return v


class FiltersV2(BaseModel):
    """Hard rejects, honored where the data exists, else degraded honestly.

    A typed object (REBUILD_PLAN §4 sketched a list-of-single-key-dicts YAML;
    a typed object is cleaner and ready for the Phase 32 validator step to
    consume — see ADR-134). ``min_quantity`` is accepted for every profile but
    only *honored* for eBay listings (the eBay API carries real quantity);
    Serper listings show stock "unknown" until the type-aware verification tier
    lands (REBUILD_PLAN §0 / §8 ``degraded_attr``).
    """

    condition_in: list[str] | None = None
    in_stock: bool | None = None
    min_quantity: int | None = Field(default=None, gt=0)


class SerperSource(BaseModel):
    """Serper.dev shopping recall — always available; on by default."""

    enabled: bool = True
    gl: str = "us"        # Google country code
    num: int = Field(default=40, gt=0)


class EbaySource(BaseModel):
    """eBay Browse API recall — off by default; the onboarder enables it per
    product (on for electronics/collectibles/apparel, off for
    subscriptions/groceries/services). REBUILD_PLAN §11 decision 2."""

    enabled: bool = False


class SourcesV2(BaseModel):
    serper: SerperSource = Field(default_factory=SerperSource)
    ebay: EbaySource = Field(default_factory=EbaySource)


class DisplaySpec(BaseModel):
    """Breadth + anti-domination knobs (REBUILD_PLAN §7 / §11 decision 3)."""

    max_listings: int = Field(default=20, gt=0)   # "10 best / 50 best" breadth
    per_vendor_cap: int = Field(default=3, gt=0)   # anti-domination
    attrs: list[str] = Field(default_factory=list)  # type-relevant display columns


# ---------------------------------------------------------------------------
# Top-level ProfileV2 model
# ---------------------------------------------------------------------------


class ProfileV2(BaseModel):
    """Validated representation of a v2 ``profile.yaml`` (query + spec shape)."""

    # Required discriminator: a v1 YAML (no schema_version, or 1) fails here.
    schema_version: Literal[2]

    slug: str
    display_name: str
    description: str = ""
    # Drives type-aware display + sensible default flags (Phase 32). Free-form
    # for now (e.g. "drone", "subscription", "book"); Phase 32 may enumerate.
    product_type: str | None = None

    target: Target  # reused from v1

    # What we send to the recall adapters. At least one query is required —
    # a profile with no query has nothing to search.
    queries: list[str] = Field(min_length=1)

    match: MatchSpec = Field(default_factory=MatchSpec)
    filters: FiltersV2 = Field(default_factory=FiltersV2)
    # ``flags`` (soft warnings; listing kept) stays permissive/open here — the
    # flag set is typed in Phase 32 when the new flag rules land.
    flags: list[dict[str, Any]] = Field(default_factory=list)

    sources: SourcesV2 = Field(default_factory=SourcesV2)
    vendor_allowlist: list[str] = Field(default_factory=list)
    vendor_blocklist: list[str] = Field(default_factory=list)

    display: DisplaySpec = Field(default_factory=DisplaySpec)

    schedule: Schedule | None = None  # reused from v1
    # ``alerts`` stays permissive — Phase 35 types the rule set (incl. the new
    # ``new_vendor_carries`` rule). The Phase 17 price rules carry over.
    alerts: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("queries")
    @classmethod
    def queries_must_be_non_empty_strings(cls, v: list[str]) -> list[str]:
        for q in v:
            if not isinstance(q, str) or not q.strip():
                raise ValueError("queries: each entry must be a non-empty string")
        return v


# ---------------------------------------------------------------------------
# Loaders (reuse v1's path resolution so the env override + repo-root logic
# stay in one place — see profile.py ``_resolve_profile_path`` / ADR-062).
# ---------------------------------------------------------------------------


def load_profile_v2_from_path(path: Path | str) -> ProfileV2:
    """Load and validate a v2 profile YAML at an explicit path.

    Raises:
        FileNotFoundError: if the YAML file does not exist.
        pydantic.ValidationError: if the YAML does not match the v2 schema.
    """
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return ProfileV2.model_validate(raw)


def load_profile_v2(slug: str) -> ProfileV2:
    """Load and validate the v2 profile for *slug*.

    Resolves the path exactly like v1 ``load_profile`` (honoring
    ``PRODUCT_SEARCH_PRODUCTS_DIR``), then validates against ``ProfileV2``.
    """
    return load_profile_v2_from_path(_resolve_profile_path(slug))


def peek_schema_version(slug: str) -> int | None:
    """Return a profile's ``schema_version`` without full validation.

    Cheap sniff used by ``cli.py`` to route ``search`` to the v1 or v2 pipeline.
    A v1 profile (no ``schema_version`` key, or ``1``) returns 1; a v2 profile
    returns 2. Returns ``None`` when the file is missing or unreadable so the
    caller can fall through to the v1 path's own not-found handling.
    """
    try:
        raw: Any = yaml.safe_load(_resolve_profile_path(slug).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    version = raw.get("schema_version")
    if version is None:
        return 1
    try:
        return int(version)
    except (TypeError, ValueError):
        return None
