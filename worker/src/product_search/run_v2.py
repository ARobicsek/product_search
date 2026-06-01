"""v2 (Serper-recall) run pipeline orchestration (Phase 32, ADR).

This is the v2 spine that ``cli.py`` routes a ``schema_version: 2`` profile to.
It ties together the Phase-31 recall + the Phase-32 deterministic edges:

    recall (Serper + eBay) → ship-from gate → vendor allow/blocklist →
    deterministic filters → ai_filter (relevance) → price-anomaly flags →
    diversity selection → type-aware JSON sidecar + lean markdown, with an
    honest run-outcome.

The pure core (``run_v2_pipeline``) does no I/O and takes the recall listings +
an injectable ``ai_filter_fn``, so the whole pipeline is unit-testable without
the network or an LLM. The ``run_v2`` wrapper adds recall, persistence, alerts
evaluation, push notification, and report writing.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from product_search.display_v2 import resolve_columns
from product_search.models import AdapterQuery, Listing
from product_search.profile_v2 import ProfileV2, load_profile_v2
from product_search.profile_v2_filter import to_filter_profile
from product_search.run_outcome import RunOutcome, classify_run_outcome
from product_search.selection import (
    SelectionResult,
    apply_vendor_filter,
    select_for_display,
)
from product_search.validators.ai_filter import ai_filter
from product_search.validators.filters import apply_filters
from product_search.validators.price_sanity import (
    annotate_price_anomalies,
    apply_ship_from_gate,
)

# An ai_filter-shaped callable: (listings, v1-filter-Profile, display_attrs) -> survivors.
AiFilterFn = Callable[..., list[Listing]]
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

    # 2. Vendor allow/blocklist — scope to the user's named vendors before the
    #    paid LLM filter sees them (REBUILD_PLAN §5.3 / Phase 33).
    vendor_scoped = apply_vendor_filter(
        gated,
        allowlist=profile.vendor_allowlist,
        blocklist=profile.vendor_blocklist,
    )

    # 3. Deterministic filters (condition_in, in_stock, title_excludes,
    #    single_sku_url — all Serper-aware already).
    det_passed = [
        lst
        for lst in vendor_scoped
        if apply_filters(lst, filter_profile.spec_filters, filter_profile) is None
    ]

    # 4. LLM relevance + spec filter (Haiku-4.5 @ temp=0, ADR-132).
    survivors = ai_filter_fn(det_passed, filter_profile, profile.display.attrs)

    # 5. Price-anomaly flags over the matched survivors (ADR-131 P1).
    annotate_price_anomalies(survivors)

    # 6. Diversity selection — cheapest-first, per-vendor cap, breadth.
    selection = select_for_display(
        survivors,
        max_listings=profile.display.max_listings,
        per_vendor_cap=profile.display.per_vendor_cap,
    )

    # 7. Type-aware display columns.
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


@dataclass
class RecallOutcome:
    """Recall listings plus per-source error flags (Phase 33).

    Each source fails independently: Serper failing does not stop eBay and vice
    versa. The booleans feed the honest run-outcome (``index_unavailable`` /
    ``ebay_unavailable``), so a failure is reported, never silently dropped.
    """

    listings: list[Listing]
    serper_error: bool = False
    ebay_error: bool = False


def _dedup_union(listings: list[Listing]) -> list[Listing]:
    """De-duplicate the cross-source union by click URL, keeping the first seen.

    Each adapter already de-dups internally (Serper by productId, eBay by
    itemId); this collapses the rare cross-source collision (the same offer
    surfaced by both). Listings with an empty URL are always kept.
    """
    seen: set[str] = set()
    out: list[Listing] = []
    for lst in listings:
        key = (lst.url or "").strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(lst)
    return out


def _default_recall(profile: ProfileV2) -> RecallOutcome:
    """Recall via the enabled shopping adapters (honors WORKER_USE_FIXTURES).

    Serper (always, when enabled) + eBay (when ``sources.ebay.enabled``); the
    queries are the same. Each adapter's auth/API errors are caught and recorded
    as a per-source flag rather than aborting the run — the other source's
    results still come back.
    """
    from product_search.adapters import ebay, serper
    from product_search.adapters.ebay import EbayAPIError, EbayAuthError
    from product_search.adapters.serper import SerperAPIError, SerperAuthError

    listings: list[Listing] = []
    serper_error = False
    ebay_error = False

    if profile.sources.serper.enabled:
        try:
            listings.extend(
                serper.fetch(
                    AdapterQuery(
                        source_id="serper_shopping",
                        queries=list(profile.queries),
                        extra={
                            "gl": profile.sources.serper.gl,
                            "num": profile.sources.serper.num,
                        },
                    )
                )
            )
        except (SerperAuthError, SerperAPIError) as exc:
            serper_error = True
            print(f"ERROR (Serper recall): {exc}", file=sys.stderr)

    if profile.sources.ebay.enabled:
        try:
            listings.extend(
                # Generic mode (no ram_specs): eBay carries real condition +
                # stock quantity; the RAM title parsing stays off for v2.
                ebay.fetch(
                    AdapterQuery(source_id="ebay_search", queries=list(profile.queries))
                )
            )
        except (EbayAuthError, EbayAPIError) as exc:
            ebay_error = True
            print(f"ERROR (eBay recall): {exc}", file=sys.stderr)

    return RecallOutcome(
        listings=_dedup_union(listings),
        serper_error=serper_error,
        ebay_error=ebay_error,
    )


def run_v2(
    slug: str,
    *,
    no_store: bool = False,
    no_report: bool = False,
    recall_fn: RecallFn | None = None,
) -> None:
    """Load a v2 profile, run the pipeline, persist + write the report sidecar."""
    import json

    profile = load_profile_v2(slug)

    # Injected recall_fn (tests) returns a plain list and is assumed error-free;
    # the default network recall returns a RecallOutcome carrying per-source flags.
    serper_error = False
    ebay_error = False
    if recall_fn is not None:
        recall_listings = recall_fn(profile)
    else:
        outcome = _default_recall(profile)
        recall_listings = outcome.listings
        serper_error = outcome.serper_error
        ebay_error = outcome.ebay_error

    result = run_v2_pipeline(
        profile, recall_listings, serper_error=serper_error, ebay_error=ebay_error
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
    csv_path: Path | None = None
    if not no_store and result.survivors:
        csv_path = _persist(slug, result.survivors)

    # --- Diff + Alerts + Push (Phase 35, REBUILD_PLAN §5 steps 7/9) --------
    alerts_md = ""
    if profile.alerts and csv_path is not None:
        from product_search.alerts import (
            evaluate_alerts,
            load_alerts_state,
            load_previous_run,
            render_audit_panel,
            save_alerts_state,
        )
        from product_search.notify import notify_material_change

        previous_listings = load_previous_run(slug, exclude=csv_path)
        alerts_state = load_alerts_state(slug)
        fired = evaluate_alerts(
            profile.alerts,
            result.survivors,
            previous_listings,
            alerts_state,
            display_name=profile.display_name,
        )
        save_alerts_state(slug, alerts_state)
        outcomes: list[bool] = []
        for fa in fired:
            ok = notify_material_change(slug, fa.headline)
            outcomes.append(ok)
            print(
                f"Alert fired ({fa.rule.kind}): {fa.headline} "
                f"(notify={'ok' if ok else 'failed'})",
                file=sys.stderr,
            )
        alerts_md = render_audit_panel(fired, outcomes)

    # --- Report sidecar (JSON source of truth) + markdown fallback. ----------
    if not no_report:
        _write_report(slug, profile, result, run_calls, snapshot_date, alerts_md=alerts_md)

    print(json.dumps([lst.to_dict() for lst in result.selection.displayed], indent=2))


def _persist(slug: str, survivors: list[Listing]) -> Path | None:
    """Best-effort history persistence (CSV + SQLite). Never fatal to a run.
    Returns the CSV path on success, None on failure."""
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
        return csv_path
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        print(f"WARNING: history persistence failed: {exc}", file=sys.stderr)
        return None


def _write_report(
    slug: str,
    profile: ProfileV2,
    result: RunV2Result,
    run_calls: list[dict[str, Any]],
    snapshot_date: date,
    alerts_md: str = "",
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

    # Price-sort survivors for the all_listings progressive-disclosure array.
    from product_search.selection import price_sort_key

    all_survivors_sorted = sorted(result.survivors, key=price_sort_key)

    payload = build_v2_payload(
        profile=profile,
        selection=result.selection,
        all_survivors=all_survivors_sorted,
        columns=result.columns,
        outcome=result.outcome,
        recall_count=result.recall_count,
        survivor_count=len(result.survivors),
        run_calls=run_calls,
        snapshot_date=snapshot_date,
    )
    json_path = default_json_path(slug, snapshot_date)
    write_json_sidecar(json_path, payload)

    md = build_v2_markdown(payload)
    if alerts_md:
        md += "\n\n" + alerts_md
    md_path = default_report_path(slug, snapshot_date)
    write_report(md_path, md)

    print(f"Wrote JSON sidecar: {json_path}", file=sys.stderr)
    print(f"Wrote report: {md_path}", file=sys.stderr)
