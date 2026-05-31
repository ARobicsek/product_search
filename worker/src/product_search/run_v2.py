"""v2 (Serper-recall) run pipeline orchestration (Phase 32, ADR).

This is the v2 spine that ``cli.py`` routes a ``schema_version: 2`` profile to.
It ties together the Phase-31 recall + the Phase-32 deterministic edges:

    recall (Serper) → ship-from gate → deterministic filters → ai_filter
    (relevance) → price-anomaly flags → diversity selection → type-aware JSON
    sidecar + lean markdown, with an honest run-outcome.

The pure core (``run_v2_pipeline``) does no I/O and takes the recall listings +
an injectable ``ai_filter_fn``, so the whole pipeline is unit-testable without
the network or an LLM. The ``run_v2`` wrapper adds recall, persistence, and
report writing.

eBay recall and alerts are out of scope here (Phase 33 / Phase 35). A profile
with ``sources.ebay.enabled`` is honored for recall in Phase 33; for now only
Serper runs (noted, never silently dropped).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from product_search.display_v2 import resolve_columns
from product_search.models import AdapterQuery, Listing
from product_search.profile_v2 import ProfileV2, load_profile_v2
from product_search.profile_v2_filter import to_filter_profile
from product_search.run_outcome import RunOutcome, classify_run_outcome
from product_search.selection import SelectionResult, select_for_display
from product_search.validators.ai_filter import ai_filter
from product_search.validators.filters import apply_filters
from product_search.validators.price_sanity import (
    annotate_price_anomalies,
    apply_ship_from_gate,
)

# An ai_filter-shaped callable: (listings, v1-filter-Profile) -> survivors.
AiFilterFn = Callable[[list[Listing], Any], list[Listing]]
RecallFn = Callable[[ProfileV2], list[Listing]]


@dataclass
class RunV2Result:
    recall_count: int
    survivors: list[Listing]          # full survivor set (persists to history)
    selection: SelectionResult        # the displayed slice + overflow
    columns: list[str]
    outcome: RunOutcome
    degraded_attrs: bool


def _allowed_countries(profile: ProfileV2) -> list[str] | None:
    """Derive the ship-from allowlist from the Serper ``gl`` market code."""
    gl = (profile.sources.serper.gl or "").strip()
    return [gl.upper()] if gl else None


def run_v2_pipeline(
    profile: ProfileV2,
    recall_listings: list[Listing],
    *,
    ai_filter_fn: AiFilterFn = ai_filter,
    serper_error: bool = False,
    ebay_error: bool = False,
) -> RunV2Result:
    """Pure pipeline core: recall listings → survivors → display selection.

    No I/O. ``ai_filter_fn`` is injectable so tests can pass a passthrough.
    """
    recall_count = len(recall_listings)
    filter_profile = to_filter_profile(profile)

    # 1. Ship-from gate (flag offers from outside the market; no-op for Serper).
    gated, _ = apply_ship_from_gate(recall_listings, _allowed_countries(profile))

    # 2. Deterministic filters (condition_in, in_stock, title_excludes,
    #    single_sku_url — all Serper-aware already).
    det_passed = [
        lst
        for lst in gated
        if apply_filters(lst, filter_profile.spec_filters, filter_profile) is None
    ]

    # 3. LLM relevance + spec filter (Haiku-4.5 @ temp=0, ADR-132).
    survivors = ai_filter_fn(det_passed, filter_profile)

    # 4. Price-anomaly flags over the matched survivors (ADR-131 P1).
    annotate_price_anomalies(survivors)

    # 5. Diversity selection — cheapest-first, per-vendor cap, breadth.
    selection = select_for_display(
        survivors,
        max_listings=profile.display.max_listings,
        per_vendor_cap=profile.display.per_vendor_cap,
    )

    # 6. Type-aware display columns.
    columns = resolve_columns(
        profile_attrs=profile.display.attrs,
        product_type=profile.product_type,
        displayed=selection.displayed,
    )

    # min_quantity is accepted but un-honorable on Serper-only recall.
    degraded = profile.filters.min_quantity is not None

    outcome = classify_run_outcome(
        recall_count=recall_count,
        survivor_count=len(survivors),
        serper_error=serper_error,
        ebay_error=ebay_error,
        degraded_attrs=degraded,
    )

    return RunV2Result(
        recall_count=recall_count,
        survivors=survivors,
        selection=selection,
        columns=columns,
        outcome=outcome,
        degraded_attrs=degraded,
    )


def _default_recall(profile: ProfileV2) -> list[Listing]:
    """Recall via the Serper shopping adapter (honors WORKER_USE_FIXTURES)."""
    from product_search.adapters import serper

    if not profile.sources.serper.enabled:
        return []
    query = AdapterQuery(
        source_id="serper_shopping",
        queries=list(profile.queries),
        extra={"gl": profile.sources.serper.gl, "num": profile.sources.serper.num},
    )
    return serper.fetch(query)


def run_v2(
    slug: str,
    *,
    no_store: bool = False,
    no_report: bool = False,
    recall_fn: RecallFn | None = None,
) -> None:
    """Load a v2 profile, run the pipeline, persist + write the report sidecar."""
    import json

    from product_search.adapters.serper import SerperAPIError, SerperAuthError

    profile = load_profile_v2(slug)
    recall = recall_fn or _default_recall

    serper_error = False
    recall_listings: list[Listing] = []
    try:
        recall_listings = recall(profile)
    except (SerperAuthError, SerperAPIError) as exc:
        serper_error = True
        print(f"ERROR (Serper recall): {exc}", file=sys.stderr)

    if profile.sources.ebay.enabled:
        print(
            "[run_v2] sources.ebay.enabled is set but eBay recall is wired in "
            "Phase 33; running Serper only this session.",
            file=sys.stderr,
        )

    result = run_v2_pipeline(
        profile, recall_listings, serper_error=serper_error
    )

    print(
        f"Recall {result.recall_count} → survivors {len(result.survivors)} → "
        f"displayed {len(result.selection.displayed)} "
        f"[{result.outcome.klass.value}].",
        file=sys.stderr,
    )

    # --- Run-cost panel (ai_filter spend; recall is a flat-fee API call). -----
    from product_search.validators import ai_filter as ai_filter_mod

    run_calls: list[dict[str, Any]] = []
    if ai_filter_mod.LAST_RUN_USAGE:
        run_calls.append(ai_filter_mod.LAST_RUN_USAGE)

    snapshot_date: date = datetime.now(tz=UTC).date()

    # --- Persist the FULL survivor set to history (REBUILD_PLAN §5.6). --------
    if not no_store and result.survivors:
        _persist(slug, result.survivors)

    # --- Report sidecar (JSON source of truth) + markdown fallback. ----------
    if not no_report:
        _write_report(slug, profile, result, run_calls, snapshot_date)

    print(json.dumps([lst.to_dict() for lst in result.selection.displayed], indent=2))


def _persist(slug: str, survivors: list[Listing]) -> None:
    """Best-effort history persistence (CSV + SQLite). Never fatal to a run."""
    try:
        from product_search.storage.csv_dump import default_csv_path, write_snapshot_csv
        from product_search.storage.db import connect, insert_listings

        conn = connect(slug)
        try:
            insert_listings(conn, survivors)
        finally:
            conn.close()
        csv_path = default_csv_path(slug, datetime.now(tz=UTC))
        write_snapshot_csv(csv_path, survivors)
        print(f"Stored {len(survivors)} survivor(s); CSV: {csv_path}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        print(f"WARNING: history persistence failed: {exc}", file=sys.stderr)


def _write_report(
    slug: str,
    profile: ProfileV2,
    result: RunV2Result,
    run_calls: list[dict[str, Any]],
    snapshot_date: date,
) -> None:
    from product_search.synthesizer import default_report_path, write_report
    from product_search.synthesizer.report_json import (
        default_json_path,
        write_json_sidecar,
    )
    from product_search.synthesizer.report_json_v2 import (
        build_v2_markdown,
        build_v2_payload,
    )

    payload = build_v2_payload(
        profile=profile,
        selection=result.selection,
        columns=result.columns,
        outcome=result.outcome,
        recall_count=result.recall_count,
        survivor_count=len(result.survivors),
        run_calls=run_calls,
        snapshot_date=snapshot_date,
    )
    json_path = default_json_path(slug, snapshot_date)
    write_json_sidecar(json_path, payload)

    md_path = default_report_path(slug, snapshot_date)
    write_report(md_path, build_v2_markdown(payload))

    print(f"Wrote JSON sidecar: {json_path}", file=sys.stderr)
    print(f"Wrote report: {md_path}", file=sys.stderr)
