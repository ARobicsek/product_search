"""Emit a structured JSON sidecar for the post-run report (ADR-096).

The React UI reads ``reports/<slug>/<date>.json`` to render the card
grid + sources panel + run-cost table; the markdown emitted by
``synthesizer.synthesize()`` (and the sources / run-cost panels
appended by cli.py) is the legacy-renderer fallback only. Both are
deterministic — no LLM contribution (the synth LLM call is retired
per ADR-096).

Source statuses are derived from ADR-084's ``classify_source_outcome``
so the table column the user sees (status pill) is honest — no more
``ok`` next to ``fetched: 0``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from datetime import date as _date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from product_search.llm.pricing import estimate_cost_usd
from product_search.models import Listing
from product_search.profile import Profile
from product_search.source_reasons import classify_source_outcome
from product_search.synthesizer.flag_labels import flags_to_badges
from product_search.synthesizer.synthesizer import (
    SYNTH_MAX_LISTINGS,
    _rank_listings,
    _source_label,
)

# Bumped only when the JSON shape changes in a way the React renderer
# must opt into. The renderer ignores sidecars whose schema_version is
# higher than it knows about and falls back to the legacy markdown view.
JSON_SCHEMA_VERSION = 1


def _vendor_host(lst: Listing) -> str | None:
    if lst.source == "universal_ai_search":
        host = (lst.attrs or {}).get("vendor_host")
        if isinstance(host, str) and host:
            return host
    parsed = urlparse(lst.url).netloc.lower()
    return parsed or None


def _listing_payload(lst: Listing, rank: int, profile: Profile) -> dict[str, Any]:
    """Shape one listing for the JSON sidecar.

    Mirrors the fields the legacy markdown table carries plus the
    human-readable badges from ``flag_labels.yaml``. Numeric fields
    are passed through verbatim (``None`` when missing) so the
    renderer can format them per locale.
    """
    price_usd = lst.kit_price_usd if lst.is_kit else lst.unit_price_usd
    attrs = lst.attrs or {}
    return {
        "rank": rank,
        "source": _source_label(lst),
        "vendor_host": _vendor_host(lst),
        "url": lst.url,
        "title": lst.title,
        "price_usd": price_usd,
        "total_for_target_usd": lst.total_for_target_usd,
        "currency_approx_fx": attrs.get("price_approx_fx"),
        "condition": lst.condition,
        "seller_name": lst.seller_name,
        "is_kit": lst.is_kit,
        "kit_module_count": lst.kit_module_count,
        "badges": flags_to_badges(list(lst.flags), profile),
    }


def _source_payload(s: dict[str, Any]) -> dict[str, Any]:
    """Shape one source's outcome for the JSON sidecar.

    Status is derived from ADR-084's ``classify_source_outcome`` —
    same classifier the legacy callout uses — so a source where
    ``fetched=0`` no longer reports ``ok``.
    """
    host = s.get("match_host")
    outcome = classify_source_outcome(
        fetched=int(s.get("fetched", 0) or 0),
        passed=int(s.get("passed", 0) or 0),
        error=s.get("error"),
        skip_reason=s.get("skip_reason"),
        diagnostics=s.get("diagnostics"),
        known_failure=None,
        dominant_rejection=s.get("dominant_rejection"),
    )
    return {
        "label": s.get("display_source") or s.get("source") or "?",
        "host": host if isinstance(host, str) else None,
        "fetched": int(s.get("fetched", 0) or 0),
        "passed": int(s.get("passed", 0) or 0),
        "status": outcome.category.value,
        "status_label": outcome.label,
        "reason": outcome.message,
        "scrappey_attempts": s.get("scrappey_attempts", []),
    }


def _pending_payload(profile: Profile) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in (getattr(profile, "sources_pending", None) or []):
        extra = getattr(p, "model_extra", None) or {}
        url = extra.get("url") if isinstance(extra, dict) else None
        pid = getattr(p, "id", None)
        if pid == "universal_ai_search":
            if isinstance(url, str) and url:
                host = urlparse(url).netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
                out.append({
                    "label": host or url,
                    "url": url,
                    "note": extra.get("note") if isinstance(extra, dict) else None,
                })
            continue
        if pid:
            out.append({
                "label": pid,
                "url": url if isinstance(url, str) else None,
                "note": extra.get("note") if isinstance(extra, dict) else None,
            })
    return out


def _run_cost_payload(calls: list[dict[str, Any]]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    total_cost = 0.0
    any_unpriced = False
    for c in calls:
        cost = estimate_cost_usd(
            str(c.get("provider", "")),
            str(c.get("model", "")),
            c.get("input_tokens"),
            c.get("output_tokens"),
        )
        if cost is None:
            any_unpriced = True
        else:
            total_cost += cost
        steps.append({
            "step": c.get("step", "?"),
            "provider": c.get("provider", ""),
            "model": c.get("model", ""),
            "input_tokens": int(c.get("input_tokens") or 0),
            "output_tokens": int(c.get("output_tokens") or 0),
            "cost_usd": cost,
        })
    return {
        "steps": steps,
        "total_usd": total_cost,
        "any_unpriced": any_unpriced,
    }


def build_json_payload(
    *,
    listings: list[Listing],
    profile: Profile,
    source_stats: list[dict[str, Any]],
    run_calls: list[dict[str, Any]],
    snapshot_date: _date | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the JSON sidecar payload.

    Uses the same ``_rank_listings`` ranking as the legacy markdown
    table so the cards visible in the React UI line up 1-to-1 with the
    table the legacy renderer would show.
    """
    ranked = _rank_listings(listings, SYNTH_MAX_LISTINGS)
    n_total_passed = len(listings)
    n_shown = len(ranked)
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "snapshot_date": snapshot_date.isoformat() if snapshot_date else None,
        "product": {
            "slug": profile.slug,
            "display_name": profile.display_name,
        },
        "listings": [
            _listing_payload(lst, i, profile) for i, lst in enumerate(ranked, 1)
        ],
        "listings_meta": {
            "total_passed": n_total_passed,
            "shown": n_shown,
            "cap": SYNTH_MAX_LISTINGS,
        },
        "sources": [_source_payload(s) for s in source_stats],
        "sources_pending": _pending_payload(profile),
        "run_cost": _run_cost_payload(run_calls),
    }


def default_json_path(slug: str, snapshot_date: _date) -> Path:
    """Mirror of ``synthesizer.report.default_report_path`` for the JSON sidecar."""
    from product_search.synthesizer.report import default_report_path

    md_path = default_report_path(slug, snapshot_date)
    return md_path.with_suffix(".json")


def write_json_sidecar(path: Path, payload: dict[str, Any]) -> Path:
    """Write the JSON sidecar to disk, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path
