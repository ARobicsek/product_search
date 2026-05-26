"""Flag-label registry loader (ADR-096).

The post-run JSON sidecar (``report_json.write_json_sidecar``) needs
each listing's flag IDs translated into human-readable badge pills for
the React card UI. The registry lives in ``flag_labels.yaml`` next to
this module; the lookup mirrors ``build_flags_md``'s three-tier walk
from ADR-095 — flag label first, originating rule name as fallback,
then the raw key as a last resort (so unmapped flags surface loudly
rather than silently as ugly jargon).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal, TypedDict

import yaml

from product_search.profile import Profile

logger = logging.getLogger(__name__)

Severity = Literal["info", "warning", "danger"]


class Badge(TypedDict):
    """Shape of one rendered badge pill on a listing card."""

    key: str
    label: str
    severity: Severity


def _default_registry_path() -> Path:
    return Path(__file__).resolve().with_name("flag_labels.yaml")


@lru_cache(maxsize=4)
def _load_registry(path_str: str | None = None) -> dict[str, dict[str, str]]:
    path = Path(path_str) if path_str else _default_registry_path()
    if not path.exists():
        logger.warning("flag_labels.yaml not found at %s", path)
        return {}
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        logger.warning("flag_labels.yaml is not a mapping at %s", path)
        return {}
    return raw


def _coerce_entry(entry: object, raw_key: str) -> Badge:
    if isinstance(entry, dict):
        label = entry.get("label", raw_key)
        severity = entry.get("severity", "info")
        if severity not in ("info", "warning", "danger"):
            severity = "info"
        return {"key": raw_key, "label": str(label), "severity": severity}
    return {"key": raw_key, "label": raw_key, "severity": "info"}


def flag_to_badge(flag: str, profile: Profile) -> Badge:
    """Resolve one flag ID to a badge pill via the ADR-095 lookup walk."""
    registry = _load_registry()

    if flag in registry:
        return _coerce_entry(registry[flag], flag)

    for rule in profile.spec_flags:
        if rule.flag == flag and rule.rule in registry:
            return _coerce_entry(registry[rule.rule], flag)

    return {"key": flag, "label": flag, "severity": "info"}


def flags_to_badges(flags: list[str], profile: Profile) -> list[Badge]:
    """Resolve a listing's flags to dedupe-stable badges, preserving order."""
    seen: set[str] = set()
    out: list[Badge] = []
    for f in flags:
        if f in seen:
            continue
        seen.add(f)
        out.append(flag_to_badge(f, profile))
    return out
