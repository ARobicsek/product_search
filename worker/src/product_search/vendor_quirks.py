"""Vendor quirks registry loader.

Single source of truth lives in ``vendor_quirks.yaml`` next to this module
(see the file header there for schema and rationale). This module exposes three pure
functions consumed by ``adapters/universal_ai.py``:

  - ``get_quirks_for_url(url)`` — raw registry entry for the URL's host
  - ``merge_alterlab_options(url, source_options)`` — merge defaults under
    explicit per-source options (source wins on conflict)
  - ``apply_url_transforms(url)`` — rewrite URL per registered transforms,
    return ``(new_url, [applied_transform_names])`` for logging

The host lookup strips a leading ``www.`` so ``www.bestbuy.com`` and
``bestbuy.com`` resolve to the same entry. Registry is cached at import
time; tests can clear the cache via ``_load_registry.cache_clear()``.

ADR-068.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

import yaml

logger = logging.getLogger(__name__)


def _default_registry_path() -> Path:
    # Package data: vendor_quirks.yaml sits beside this module.
    return Path(__file__).resolve().with_name("vendor_quirks.yaml")


def _normalize_host(host: str) -> str:
    host = (host or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


@lru_cache(maxsize=4)
def _load_registry(path_str: str | None = None) -> dict[str, dict[str, Any]]:
    path = Path(path_str) if path_str else _default_registry_path()
    if not path.exists():
        logger.warning("vendor_quirks.yaml not found at %s", path)
        return {}
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        logger.error(
            "vendor_quirks.yaml root must be a mapping, got %s", type(raw).__name__
        )
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        out[_normalize_host(k)] = v
    _check_alterlab_known_good_consistency(out)
    return out


def _check_alterlab_known_good_consistency(registry: dict[str, dict[str, Any]]) -> None:
    """Registry-load lint for `alterlab_known_good` mis-tags.

    Two distinct inconsistencies are flagged (ADR-082, refined by ADR-088):

    1. `alterlab_known_good: true` WITHOUT `default_alterlab_options`. The flag
       asserts AlterLab renders this vendor fine in production. For any
       JS-rendered vendor (Amazon, Backmarket, …) that claim is only
       deliverable if the runtime also passes a `wait_condition` so the DOM
       settles before capture. A host with the flag but no defaults can
       silently regress to 'bare-tier AlterLab returns 1+ MB of empty chrome' —
       the exact Phase 23 Part A Amazon failure (commit a1f98dc, 2026-05-24).
       Two EXEMPTIONS (ADR-088): a host carrying a `known_failure` block is
       explicitly broken (Cloudflare-walled etc.) so render defaults can't fix
       it; and a host carrying a `dedicated_adapter` has its recall owned by a
       bespoke adapter, not the universal_ai render path, so the render-defaults
       premise simply doesn't apply (e.g. ebay.com → `ebay_search`).

    2. `alterlab_known_good: true` AND a `known_failure` block together. These
       are mutually exclusive assertions — "AlterLab handles this host fine"
       vs "this host has no working path". This is the exact mis-tag that hid
       centralcomputer.com / serversupply.com behind a known-good flag while
       they were in fact Cloudflare-walled (ADR-088).

    This check fires at registry load so a gap is loud at import time
    (including under pytest collection).
    """
    for host, entry in registry.items():
        if entry.get("alterlab_known_good") is not True:
            continue
        has_known_failure = isinstance(entry.get("known_failure"), dict)
        if has_known_failure:
            logger.warning(
                "[vendor_quirks] %s has BOTH `alterlab_known_good: true` and a "
                "`known_failure` block — these contradict (known-good asserts "
                "AlterLab works; known_failure asserts no working path). Drop "
                "one (ADR-088).",
                host,
            )
            continue
        # Recall owned by a dedicated adapter → the universal_ai render-defaults
        # heuristic doesn't apply to this host (ADR-088).
        if entry.get("dedicated_adapter"):
            continue
        defaults = entry.get("default_alterlab_options")
        if isinstance(defaults, dict) and defaults:
            continue
        logger.warning(
            "[vendor_quirks] %s has `alterlab_known_good: true` but no "
            "`default_alterlab_options` — JS-rendered vendors will silently "
            "fetch empty chrome (ADR-082). Add at minimum "
            "`wait_condition: networkidle`.",
            host,
        )


def get_quirks_for_host(host: str) -> dict[str, Any]:
    return _load_registry().get(_normalize_host(host), {})


def render_search_url(host: str, query: str) -> str | None:
    """Render a vendor's registered search-results URL for ``query`` (ADR-105).

    Returns ``None`` when the host has no ``search_url_template`` (the onboarder
    then falls back to constructing a URL itself). The template's ``{q}``
    placeholder is replaced with the URL-encoded keywords (spaces -> ``+``), so
    the param name (``Ntt``, ``d``, ``searchTerm`` …) comes from the registry,
    never from an LLM guess. This is the single source of truth shared with the
    TypeScript ``renderSearchUrl`` (parity-checked, like ``_build_alterlab_body``).
    """
    template = get_quirks_for_host(host).get("search_url_template")
    if not isinstance(template, str) or "{q}" not in template:
        return None
    return template.replace("{q}", quote_plus(query.strip()))


def get_quirks_for_url(url: str) -> dict[str, Any]:
    try:
        host = urlparse(url).netloc
    except Exception:
        return {}
    return get_quirks_for_host(host)


# Valid AlterLab `advanced.wait_condition` values (ADR-071 / docs/ALTERLAB_OPTIONS.md).
_VALID_WAIT_CONDITIONS = {"domcontentloaded", "networkidle", "load"}


def normalize_alterlab_options(
    options: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate + migrate an ``alterlab_options`` dict before it hits the wire.

    ADR-071. AlterLab has **no** ``wait_for`` parameter. Sending one (a numeric
    "seconds" value from the old registry, or a CSS selector the onboarder was
    once told to pass) forces the request into an async 202 job that never
    resolves -> the adapter polls to timeout and returns body 0 (the B&H /
    Target body-0 failures). The real knob is ``advanced.wait_condition``
    (``domcontentloaded`` | ``networkidle`` | ``load``).

    This function is the single choke point that keeps a malformed option off
    the wire:
      * legacy ``wait_for`` (any value) -> dropped, mapped to
        ``wait_condition: "networkidle"`` (its original "wait for JS" intent)
        unless an explicit ``wait_condition`` is already present;
      * an invalid ``wait_condition`` is dropped (logged);
      * ``min_tier`` is clamped to 1..4.

    Returns ``None`` when the cleaned dict is empty, matching the
    "no options -> simple fetch" contract of :func:`merge_alterlab_options`.
    """
    if not options:
        return None
    out: dict[str, Any] = dict(options)

    legacy_wait_for = out.pop("wait_for", None)
    if legacy_wait_for is not None and "wait_condition" not in out:
        out["wait_condition"] = "networkidle"
        logger.info(
            "[vendor_quirks] migrated legacy alterlab_options.wait_for=%r -> "
            "wait_condition='networkidle' (ADR-071)",
            legacy_wait_for,
        )

    wc = out.get("wait_condition")
    if wc is not None and wc not in _VALID_WAIT_CONDITIONS:
        logger.warning(
            "[vendor_quirks] dropping invalid wait_condition=%r (valid: %s)",
            wc,
            sorted(_VALID_WAIT_CONDITIONS),
        )
        out.pop("wait_condition", None)

    mt = out.get("min_tier")
    if mt is not None:
        try:
            out["min_tier"] = max(1, min(4, int(mt)))
        except (TypeError, ValueError):
            out.pop("min_tier", None)

    sa = out.get("skip_alterlab")
    if sa is not None:
        out["skip_alterlab"] = bool(sa)

    return out or None


def merge_alterlab_options(
    url: str,
    source_options: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge vendor defaults with source-level options. Source wins on key conflict.

    Returns ``None`` when neither side contributes any options, so callers
    can keep the existing "no options → simple raw fetch" code path. The
    merged result is normalized (ADR-071) so a legacy ``wait_for`` from either
    the registry or a serialized profile can never reach the wire.
    """
    quirks = get_quirks_for_url(url)
    defaults = quirks.get("default_alterlab_options")
    if not isinstance(defaults, dict):
        defaults = None
    if not defaults and not source_options:
        return None
    merged: dict[str, Any] = {}
    if defaults:
        merged.update(defaults)
    if source_options:
        # Source-level keys override defaults — explicit user intent wins.
        for k, v in source_options.items():
            if v is not None:
                merged[k] = v
    return normalize_alterlab_options(merged)


def apply_url_transforms(url: str) -> tuple[str, list[str]]:
    """Apply registered URL transforms for ``url``'s host.

    Returns ``(possibly_rewritten_url, applied_transform_labels)``. The label
    list is empty when no transform matched, which lets the caller skip
    logging entirely on the common case.
    """
    quirks = get_quirks_for_url(url)
    transforms = quirks.get("url_transforms")
    if not isinstance(transforms, list) or not transforms:
        return url, []

    try:
        parsed = urlparse(url)
    except Exception:
        return url, []

    applied: list[str] = []
    new_query = parsed.query
    host_label = _normalize_host(parsed.netloc)

    for i, t in enumerate(transforms):
        if not isinstance(t, dict):
            continue
        when = t.get("when") if isinstance(t.get("when"), dict) else {}

        if isinstance(when, dict):
            prefix = when.get("path_prefix")
            if isinstance(prefix, str) and not parsed.path.startswith(prefix):
                continue
            includes = when.get("path_includes")
            if isinstance(includes, str) and includes not in parsed.path:
                continue
            q_includes = when.get("query_includes")
            if isinstance(q_includes, dict):
                current_q = dict(parse_qsl(new_query, keep_blank_values=True))
                if not all(
                    str(current_q.get(k)) == str(v) for k, v in q_includes.items()
                ):
                    continue

        ap = t.get("append_query")
        if isinstance(ap, dict) and ap:
            current_q = dict(parse_qsl(new_query, keep_blank_values=True))
            changed = False
            for k, v in ap.items():
                key = str(k)
                if key not in current_q:
                    current_q[key] = str(v)
                    changed = True
            if changed:
                new_query = urlencode(current_q)
                applied.append(f"{host_label}.append_query[{i}]")

    if not applied:
        return url, []
    return urlunparse(parsed._replace(query=new_query)), applied
