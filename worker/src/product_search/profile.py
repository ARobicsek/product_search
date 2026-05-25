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

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Allow-list of adapter IDs that may appear in ``sources[].id``
# (and ``sources_pending[].id``).  Add entries as new adapters land.
# ---------------------------------------------------------------------------
KNOWN_SOURCE_IDS: frozenset[str] = frozenset(
    [
        "ebay_search",
        "universal_ai_search",
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
        "condition_in",
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
# Allow-list of report-table column ids. Must match the keys of
# ``synthesizer.COLUMN_DEFS``. Kept here (not imported) to avoid an
# import cycle and to keep ``profile.py`` self-contained.
# ---------------------------------------------------------------------------
KNOWN_REPORT_COLUMNS: frozenset[str] = frozenset(
    [
        "rank",
        "source",
        "title",
        "pack_size",
        "price_pack",
        "price_unit",
        "total_for_target",
        "qty",
        "condition",
        "brand",
        "mpn",
        "seller",
        "seller_rating",
        "ship_from",
        "qvl_status",
        "flags",
        "flavor",
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
    # ``configurations`` is the RAM-domain mechanism for "how to reach the
    # target capacity" (e.g. 8x32GB or 4x64GB to reach 256GB). Non-RAM
    # products (single-unit consumer goods like headphones, paintball
    # pistols) have nothing meaningful to put here, so the list defaults
    # to empty. The ``min_quantity_for_target`` filter rule and the
    # ``total_for_target_usd`` synthesizer column both no-op when the
    # list is empty.
    configurations: list[TargetConfiguration] = Field(default_factory=list)


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
    # Plain-English explanation of what this flag means; surfaced in the
    # report's deterministic Flags section. Optional — if absent, the
    # synthesizer falls back to a built-in dict of stable flag IDs and
    # then to the bare flag id.
    description: str | None = None
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
    # Optional opt-in for the universal_ai_search adapter's Tier 1.5
    # detail-page extractor (ADR-049). ``"detail"`` means the URL is a
    # single-product detail page (one exact SKU, no JSON-LD, only nav-junk
    # anchors) — route it to the bounded detail-LLM tier instead of the
    # anchor tier. ``"search"`` forces the anchor/search tier. When absent
    # the adapter falls back to a URL-shape heuristic. MUST stay in sync
    # with the TS mirror in web/lib/onboard/schema.ts.
    page_type: Literal["detail", "search"] | None = None
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

    @model_validator(mode="after")
    def validate_universal_ai_url(self) -> Source:
        if self.id == "universal_ai_search":
            url = getattr(self, "url", None)
            if not url or not isinstance(url, str):
                raise ValueError("universal_ai_search source must have a 'url' string field.")
            from urllib.parse import urlparse
            parsed = urlparse(url)
            path = parsed.path.rstrip("/")
            if not path:
                raise ValueError(
                    f"URL {url!r} is a bare domain. A search URL with parameters or a valid path is required."
                )
        return self


class PendingSource(BaseModel):
    id: str
    note: str | None = None
    model_config = {"extra": "allow"}


class PriceBelowAlert(BaseModel):
    """Fire when the cheapest passing listing's price_unit is/drops below
    ``threshold_usd``. Optional ``condition`` filter restricts which listings
    count toward the cheapest (e.g. "new" only).

    ``mode`` controls re-fire semantics (ADR-056):

    - ``drops_below`` (default; back-compat with pre-ADR-056 rules): fires only
      on the *transition* run where the matching cheapest crosses from at/above
      the threshold down to below it (or on the first run when there is no
      previous run). Will NOT fire if the price was already below when the rule
      was created.
    - ``is_below``: fires as soon as the matching cheapest is below the
      threshold — including immediately on the first run after the rule is
      created while already below — then stays quiet for the rest of that dip
      and re-arms once the price goes back to/above the threshold. State is
      persisted per-rule in ``reports/<slug>/alerts_state.json``.
    - ``while_below`` (ADR-057): fires on *every* run where the matching
      cheapest is below the threshold (no dedupe). Stateless — never touches
      ``alerts_state.json``. A run with no eligible listing simply does not
      fire that run (ship-simple; robust source-error handling is the deferred
      ADR-053 item).

    ``price_basis`` (ADR-059) selects which price the threshold compares
    against:

    - ``unit`` (default; back-compat): ``unit_price_usd`` — the price of one
      module. Unchanged behavior for any pre-existing serialized rule.
    - ``total``: the listing's as-sold price — ``kit_price_usd`` for a kit,
      else ``unit_price_usd`` (a single item's as-sold price *is* its unit
      price). "Cheapest" is also re-ranked by this basis.
    """

    kind: Literal["price_below"]
    threshold_usd: float = Field(gt=0)
    condition: Literal["new", "used", "refurbished"] | None = None
    mode: Literal["drops_below", "is_below", "while_below"] = "drops_below"
    price_basis: Literal["unit", "total"] = "unit"


class VendorSeenAlert(BaseModel):
    """Fire when ≥1 passing listing has its vendor host equal to ``host``
    (canonical match per ADR-020). Fired on transition only: only fires when
    previous run had 0 passing listings for this host (or no previous run).
    """

    kind: Literal["vendor_seen"]
    host: str = Field(min_length=1)


# Discriminated union of all alert rule kinds. Add new kinds here, give them
# a unique ``kind`` literal, and update the TS mirror in web/lib/onboard/schema.ts.
AlertRule = Annotated[
    PriceBelowAlert | VendorSeenAlert,
    Field(discriminator="kind"),
]


class Schedule(BaseModel):
    """A product's run schedule.

    Exactly one of ``cron`` (recurring) or ``run_at`` (one-time) must be set.

    - ``cron`` is a standard 5-field expression interpreted in UTC. The
      scheduler now honours the *minute* field too (Phase 17 follow-up), not
      just the hour.
    - ``run_at`` is an absolute UTC instant for a single run. After the
      scheduler fires it, it strips the whole ``schedule:`` block from the
      profile so the run never repeats.

    ``timezone`` stays ``UTC``. The web UI lets the user pick a wall-clock
    time in their local zone and converts to UTC before it reaches this
    model (see ``web/lib/schedule.ts``); we never store a non-UTC zone.
    """

    cron: str | None = None
    run_at: datetime | None = None
    timezone: Literal["UTC"] = "UTC"

    @field_validator("cron")
    @classmethod
    def cron_must_be_valid(cls, v: str | None) -> str | None:
        """Validate a 5-field cron expression (minute hour dom month dow)."""
        if v is None:
            return v
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

    @field_validator("run_at")
    @classmethod
    def run_at_must_be_utc(cls, v: datetime | None) -> datetime | None:
        """Normalise ``run_at`` to an aware UTC datetime. A naive value is
        interpreted as UTC (the UI always emits a ``...Z`` instant)."""
        if v is None:
            return v
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def exactly_one_mode(self) -> Schedule:
        has_cron = self.cron is not None
        has_run_at = self.run_at is not None
        if has_cron and has_run_at:
            raise ValueError(
                "schedule must set either 'cron' (recurring) or 'run_at' "
                "(one-time), not both"
            )
        if not has_cron and not has_run_at:
            raise ValueError(
                "schedule must set one of 'cron' (recurring) or 'run_at' "
                "(one-time)"
            )
        return self


# ---------------------------------------------------------------------------
# Top-level Profile model
# ---------------------------------------------------------------------------


class Profile(BaseModel):
    """Validated representation of a ``products/<slug>/profile.yaml`` file."""

    slug: str
    display_name: str
    # ``description`` is informational flavor text only — the AI filter prompt
    # reads it for context but falls back to ``display_name`` when it is empty
    # or missing (ai_filter.py). Making it optional defends against onboarder
    # drafts that silently omit the field (ADR-074 followup #2): the field is
    # not load-bearing, and rejecting the save for a non-load-bearing gap
    # costs the user a round-trip while the model has already correctly
    # captured ``display_name`` and ``target``.
    description: str = ""

    target: Target
    # ``spec_attrs`` declares the typed attribute keys listings may carry.
    # Most non-RAM domains (headphones, apparel, single-SKU consumer goods)
    # have nothing useful to put here; the validator pipeline does not require
    # any keys to be present, so the default is an empty dict.
    spec_attrs: dict[str, SpecAttrDef] = Field(default_factory=dict)
    spec_filters: list[FilterRule] = Field(default_factory=list)
    spec_flags: list[FlagRule] = Field(default_factory=list)

    sources: list[Source] = Field(min_length=1)
    sources_pending: list[PendingSource] = Field(default_factory=list)

    # ``qvl_file`` is the path to a Qualified Vendor List YAML — a RAM-domain
    # concept (manufacturer-published lists of validated DIMM part numbers).
    # Non-RAM products have no analogous reference data, so this is optional.
    # When None, the run pipeline skips QVL annotation entirely.
    qvl_file: str | None = None

    synthesis_hints: list[str] = Field(default_factory=list)

    # Optional per-product override of the daily report's ranked-listings
    # table columns. When unset (None) the synthesizer's
    # ``DEFAULT_REPORT_COLUMNS`` is used. Each id must be in
    # ``KNOWN_REPORT_COLUMNS``. The order in this list is the order the
    # columns appear in the report.
    report_columns: list[str] | None = None

    # Optional list of brand names the validator pipeline should look
    # for in listing titles when an adapter leaves ``brand`` as ``None``.
    # Match is case-insensitive, word-boundary. The first candidate
    # found is assigned to ``listing.brand`` in its declared casing.
    # Used because eBay's Browse API summary endpoint doesn't reliably
    # populate ``brand`` for non-RAM categories (e.g. headphones).
    brand_candidates: list[str] | None = None

    # ``schedule`` is optional: a profile without a schedule is run only via the
    # web "Run now" button (or manually). The hourly scheduler skips profiles
    # whose schedule is None.
    schedule: Schedule | None = None

    # ``alerts`` are user-supplied (NOT proposed by the onboarder LLM); the
    # web schedule editor is the only writer. The post-run alerts evaluator
    # compares each rule to the previous run's snapshot and fires push
    # notifications via /api/push/notify on state transitions only. Default
    # empty — most profiles have no alerts.
    alerts: list[AlertRule] = Field(default_factory=list)

    @field_validator("brand_candidates")
    @classmethod
    def brand_candidates_must_be_non_empty_strings(
        cls, v: list[str] | None
    ) -> list[str] | None:
        if v is None:
            return v
        if len(v) == 0:
            raise ValueError("brand_candidates: list must be non-empty if provided")
        for c in v:
            if not isinstance(c, str) or not c.strip():
                raise ValueError("brand_candidates: each entry must be a non-empty string")
        return v

    @field_validator("report_columns")
    @classmethod
    def report_columns_must_be_known(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) == 0:
            raise ValueError("report_columns: list must be non-empty if provided")
        unknown = [c for c in v if c not in KNOWN_REPORT_COLUMNS]
        if unknown:
            raise ValueError(
                f"Unknown report column(s) {unknown!r}. "
                f"Known columns: {sorted(KNOWN_REPORT_COLUMNS)}"
            )
        if len(set(v)) != len(v):
            raise ValueError("report_columns: must not contain duplicates")
        return v

    @model_validator(mode="after")
    def slug_matches_qvl_path(self) -> Profile:
        """qvl_file (when set) must contain the profile's own slug."""
        if self.qvl_file is not None and self.slug not in self.qvl_file:
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


# Env override: when set, profiles/QVL are resolved as
# ``$PRODUCT_SEARCH_PRODUCTS_DIR/<slug>/{profile,qvl}.yaml`` instead of the
# repo's ``products/`` tree. The point is decoupling the test suite + CI from
# the live ``products/`` directory, which the deployed web app rewrites and
# deletes from on its own (it commits straight to origin/main). A profile that
# tests + CI depend on must live somewhere the app never touches; this hook
# lets the CLI integration tests (and the CI ``validate`` step) point the
# loader at the committed ``worker/tests/fixtures/profiles`` tree. Unset in
# production, so normal runs are unchanged. See ADR-062.
_PRODUCTS_DIR_ENV = "PRODUCT_SEARCH_PRODUCTS_DIR"


def _products_dir_override() -> Path | None:
    raw = os.environ.get(_PRODUCTS_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def _resolve_profile_path(slug: str) -> Path:
    """Return the absolute path to ``<products>/<slug>/profile.yaml``."""
    override = _products_dir_override()
    if override is not None:
        path = override / slug / "profile.yaml"
        if path.exists():
            return path
        raise FileNotFoundError(
            f"Profile not found for slug {slug!r} under "
            f"{_PRODUCTS_DIR_ENV}={override}. Tried:\n  {path}"
        )
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


def _resolve_qvl_path(slug: str) -> Path:
    """Return the absolute path to ``<products>/<slug>/qvl.yaml``."""
    override = _products_dir_override()
    if override is not None:
        return override / slug / "qvl.yaml"
    repo_root = _repo_root()
    path = repo_root / "products" / slug / "qvl.yaml"
    if not path.exists():
        path = Path.cwd().parent / "products" / slug / "qvl.yaml"
    return path


def load_profile_from_path(path: Path | str) -> Profile:
    """Load and validate a profile YAML at an explicit path.

    Raises:
        FileNotFoundError: if the YAML file does not exist.
        pydantic.ValidationError: if the YAML does not match the schema.
    """
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Profile.model_validate(raw)


def load_qvl_from_path(path: Path | str) -> QVL:
    """Load and validate a QVL YAML at an explicit path.

    Raises:
        FileNotFoundError: if the YAML file does not exist.
        pydantic.ValidationError: if the YAML does not match the schema.
    """
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return QVL.model_validate(raw)


def load_profile(slug: str) -> Profile:
    """Load and validate the profile for *slug*.

    Raises:
        FileNotFoundError: if the YAML file does not exist.
        pydantic.ValidationError: if the YAML does not match the schema.
    """
    return load_profile_from_path(_resolve_profile_path(slug))


def load_qvl(slug: str) -> QVL:
    """Load and validate the QVL file for *slug*.

    Raises:
        FileNotFoundError: if the YAML file does not exist.
        pydantic.ValidationError: if the YAML does not match the schema.
    """
    return load_qvl_from_path(_resolve_qvl_path(slug))
