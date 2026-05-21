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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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
    return out


def get_quirks_for_host(host: str) -> dict[str, Any]:
    return _load_registry().get(_normalize_host(host), {})


def get_quirks_for_url(url: str) -> dict[str, Any]:
    try:
        host = urlparse(url).netloc
    except Exception:
        return {}
    return get_quirks_for_host(host)


def merge_alterlab_options(
    url: str,
    source_options: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge vendor defaults with source-level options. Source wins on key conflict.

    Returns ``None`` when neither side contributes any options, so callers
    can keep the existing "no options → simple raw fetch" code path.
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
    return merged or None


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
