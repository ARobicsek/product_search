"""CLI entry point for the product-search worker.

Commands are added per-phase. This file is the stable entry point;
sub-commands live in their respective modules.

Phase 1: validate <slug>
Phase 2: llm-ping <provider> <model>
         search <slug>
Phase 4: diff <slug>
Phase 5: search <slug> writes reports/<slug>/<date>.md via the synthesizer
Phase 7: scheduler-tick
"""

import argparse
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

load_dotenv()

if TYPE_CHECKING:
    from product_search.models import Listing


def _passed_match_key(lst: "Listing") -> tuple[str, str | None, str | None]:
    """Tuple key used to attribute a passing listing back to its source-stats row.

    Mirror the ``source_stats`` key shape ``(source_id, vendor_host_or_None,
    source_url_or_None)``. Multiple ``universal_ai_search`` source entries —
    one per vendor URL — all share ``lst.source == 'universal_ai_search'`` and
    often share the same ``vendor_host`` too (e.g. four Best Buy detail URLs
    are all ``bestbuy.com``). Without the URL tiebreaker each same-host row
    would claim the full per-host passed-count — the Phase 26 bug where three
    ``bestbuy.com | error: HTTPError ...`` rows each rendered ``Passed | 2``
    and the ADR-084 callout silently classified them as ``OK``.

    universal_ai listings carry ``vendor_host`` AND ``source_url`` in
    ``attrs`` (the host set by the adapter at emit time; ``source_url`` is
    stamped by this cli loop right after the adapter returns, so it's always
    the EXACT URL of the source-row that produced the listing). Other
    adapters set neither, so both fall back to None and match the source row
    that also has ``match_host=None`` / ``match_url=None``.
    """
    if lst.source == "universal_ai_search":
        attrs = lst.attrs or {}
        host = attrs.get("vendor_host")
        host_key: str | None = None
        if isinstance(host, str):
            h = host.lower()
            if h.startswith("www."):
                h = h[4:]
            host_key = h or None
        url = attrs.get("source_url")
        url_key = url if isinstance(url, str) and url else None
        return (lst.source, host_key, url_key)
    return (lst.source, None, None)


def annotate_dominant_rejections(
    source_stats: list[dict[str, Any]],
    rejection_log: list[dict[str, Any]],
) -> None:
    """Set ``dominant_rejection`` on each source-stats row, in place (ADR-109).

    ADR-098 fix #4 surfaces a "your search URL may be mis-scoped" message when a
    source's rejections are predominantly ``relevance_check`` (the page returned
    unrelated products). That only works if rejections are attributed to the
    *right* source. Every ``universal_ai_search`` row shares ``source ==
    'universal_ai_search'``, so keying attribution by the adapter id (the old
    behaviour) lumped all universal sources together and let one mis-scoped
    vendor's relevance rejections bleed onto unrelated vendors (or be diluted
    below the 50% threshold). The ai_filter rejection log now carries
    ``source_url`` (the exact URL the listing was fetched from), so we key by
    ``match_url`` per universal source and fall back to the adapter id only for
    dedicated single-source adapters (ebay_search, etc.).
    """
    rejected = [e for e in rejection_log if not e.get("pass")]
    for s in source_stats:
        fetched = int(s.get("fetched", 0) or 0)
        passed = int(s.get("passed", 0) or 0)

        # 1. 0 results pre-filter
        if fetched == 0:
            s["dominant_rejection"] = "vendor_does_not_carry"
            continue

        match_url = s.get("match_url")
        if isinstance(match_url, str) and match_url:
            src_rejected = [e for e in rejected if e.get("source_url") == match_url]
        else:
            src_id = s.get("source")
            src_rejected = [e for e in rejected if e.get("source") == src_id]
        
        if not src_rejected:
            continue
            
        # 2. ALL results were rejected by the AI filter
        if fetched > 0 and passed == 0 and len(src_rejected) >= fetched:
            s["dominant_rejection"] = f"mis_scoped_url_{len(src_rejected)}"
            continue

        relevance_count = sum(
            1
            for e in src_rejected
            if "relevance_check" in str(e.get("reason", "")).lower()
        )
        if relevance_count >= len(src_rejected) * 0.5:
            s["dominant_rejection"] = "relevance_check"


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="product-search",
        description="Product Search Worker CLI",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # Phase 1: validate
    validate_parser = subparsers.add_parser(
        "validate", help="Validate a product profile against the schema"
    )
    validate_parser.add_argument("slug", help="Product slug (e.g. ddr5-rdimm-256gb)")

    # Phase 2: llm-ping
    ping_parser = subparsers.add_parser(
        "llm-ping", help="Send a hello-world round-trip to an LLM provider"
    )
    ping_parser.add_argument(
        "provider",
        choices=["anthropic", "openai", "gemini", "glm"],
        help="LLM provider name",
    )
    ping_parser.add_argument(
        "model",
        help='Model identifier (e.g. "claude-haiku-4-5", "gpt-4o-mini")',
    )

    # Phase 2: search
    search_parser = subparsers.add_parser(
        "search",
        help="Fetch listings for a product slug (stdout JSON)",
    )
    search_parser.add_argument("slug", help="Product slug (e.g. ddr5-rdimm-256gb)")
    search_parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip schema validation of the profile before searching",
    )
    search_parser.add_argument(
        "--no-store",
        action="store_true",
        help="Do not write results to SQLite or to the per-run CSV dump",
    )
    search_parser.add_argument(
        "--no-report",
        action="store_true",
        help="Do not run the synthesizer or write reports/<slug>/<date>.md",
    )

    # Phase 4: diff
    diff_parser = subparsers.add_parser(
        "diff",
        help="Show new / dropped / price-changed listings between the two most "
             "recent daily snapshots",
    )
    diff_parser.add_argument("slug", help="Product slug (e.g. ddr5-rdimm-256gb)")

    # Phase 7: scheduler-tick
    subparsers.add_parser(
        "scheduler-tick",
        help="Run search for all profiles whose cron matches the current UTC hour",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if hasattr(args, "slug") and isinstance(args.slug, str):
        args.slug = args.slug.strip()

    if args.command == "validate":
        _cmd_validate(args.slug)

    elif args.command == "llm-ping":
        _cmd_llm_ping(args.provider, args.model)

    elif args.command == "search":
        # Route schema_version: 2 profiles to the v2 (Serper-recall) pipeline;
        # everything else stays on the legacy v1 path (Phase 32, ADR).
        from product_search.profile_v2 import peek_schema_version

        if peek_schema_version(args.slug) == 2:
            from product_search.run_v2 import run_v2

            run_v2(
                args.slug,
                no_store=args.no_store,
                no_report=args.no_report,
            )
        else:
            _cmd_search(
                args.slug,
                no_validate=args.no_validate,
                no_store=args.no_store,
                no_report=args.no_report,
            )

    elif args.command == "diff":
        _cmd_diff(args.slug)

    elif args.command == "scheduler-tick":
        _cmd_scheduler_tick()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _cmd_validate(slug: str) -> None:
    """Load and validate the profile + QVL for *slug*.

    Exits 0 on success, 1 on validation failure, 2 on file-not-found.
    """
    from pydantic import ValidationError

    from product_search.profile import load_profile, load_qvl

    try:
        profile = load_profile(slug)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    except ValidationError as exc:
        print(f"INVALID profile for {slug!r}:", file=sys.stderr)
        print(exc, file=sys.stderr)
        sys.exit(1)

    print(f"[ok] profile.yaml  ({profile.display_name})")

    if profile.qvl_file is None:
        print("[ok] qvl_file       (not set; QVL is RAM-only and skipped)")
    else:
        try:
            qvl = load_qvl(slug)
            print(f"[ok] qvl.yaml      ({len(qvl.qvl)} entries)")
        except FileNotFoundError:
            print("[warn] qvl.yaml not found -- skipping QVL check")
        except ValidationError as exc:
            print(f"INVALID qvl.yaml for {slug!r}:", file=sys.stderr)
            print(exc, file=sys.stderr)
            sys.exit(1)

    print(f"\nProfile {slug!r} is valid.")
    sys.exit(0)


# ---------------------------------------------------------------------------
# llm-ping
# ---------------------------------------------------------------------------


def _cmd_llm_ping(provider: str, model: str) -> None:
    """Send a hello-world message to the given provider/model.

    Exits 0 on success, 1 on any error.
    """
    from product_search.llm import LLMError, Message, call_llm

    print(f"Pinging {provider} / {model} ...")
    try:
        resp = call_llm(
            provider=provider,  # type: ignore[arg-type]
            model=model,
            system="You are a terse assistant. Reply in one sentence.",
            messages=[Message(role="user", content="Say hello and state your model name.")],
            max_tokens=64,
        )
    except (LLMError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Response: {resp.text.strip()}")
    if resp.input_tokens is not None:
        print(f"Tokens:   in={resp.input_tokens}  out={resp.output_tokens}")
    print("OK")
    sys.exit(0)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def _cmd_search(
    slug: str,
    *,
    no_validate: bool = False,
    no_store: bool = False,
    no_report: bool = False,
) -> None:
    """Fetch listings for *slug* and print as JSON to stdout.

    Uses WORKER_USE_FIXTURES=1 for fixture-based offline runs.
    Exits 0 on success, 1 on error.
    """
    import json
    import os
    from datetime import UTC, datetime
    from typing import Any

    from product_search.models import AdapterQuery
    from product_search.profile import QVL, Source, load_profile, load_qvl

    # --- Load profile ---------------------------------------------------------
    if not no_validate:
        from pydantic import ValidationError

        try:
            profile = load_profile(slug)
            qvl = load_qvl(slug) if profile.qvl_file is not None else QVL(qvl=[])
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        except ValidationError as exc:
            print(f"INVALID profile for {slug!r}:", file=sys.stderr)
            print(exc, file=sys.stderr)
            sys.exit(1)
    else:
        profile = load_profile(slug)
        qvl = load_qvl(slug) if profile.qvl_file is not None else QVL(qvl=[])

    use_fixtures = os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes")
    mode = "fixture" if use_fixtures else "live"
    source_names = ", ".join(sorted(list({s.id for s in profile.sources})))
    print(f"Searching {slug!r} via {source_names} [{mode} mode] ...", file=sys.stderr)

    run_started_at = datetime.now(tz=UTC)
    snapshot_date = run_started_at.date()

    # --- Run adapters ---------------------------------------------------------
    from product_search.models import Listing

    all_listings: list[Listing] = []
    source_stats: list[dict[str, Any]] = []
    from concurrent.futures import ThreadPoolExecutor

    def _process_source(source: Source) -> tuple[list[Listing], dict[str, Any]]:
        query = AdapterQuery.from_profile_source(source.model_dump())
        listings: list[Listing] = []
        error_msg: str | None = None
        # ADR-084: extra per-source signal for the source-reason classifier.
        skip_reason: str | None = None
        diagnostics: dict[str, Any] | None = None

        try:
            if source.id == "ebay_search":
                from product_search.adapters.ebay import EbayAuthError
                from product_search.adapters.ebay import fetch as fetch_ebay
                try:
                    listings = fetch_ebay(query)
                except EbayAuthError as exc:
                    print(f"ERROR (eBay auth): {exc}", file=sys.stderr)
                    print(
                        "Tip: set WORKER_USE_FIXTURES=1 to use saved fixtures "
                        "while waiting for eBay API credentials.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
            elif source.id == "nemixram_storefront":
                from product_search.adapters.nemixram import fetch as fetch_nemixram
                listings = fetch_nemixram(query)
            elif source.id == "cloudstoragecorp_ebay":
                from product_search.adapters.cloudstoragecorp import fetch as fetch_cloud
                listings = fetch_cloud(query)
            elif source.id == "memstore_ebay":
                from product_search.adapters.memstore import fetch as fetch_memstore
                listings = fetch_memstore(query)
            else:
                error_msg = "no adapter wired"
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            print(f"ERROR ({source.id} fetch): {exc}", file=sys.stderr)

        stat = {
            "source": source.id,
            "match_host": None,
            "match_url": None,
            "display_source": source.id,
            "fetched": len(listings),
            "error": error_msg,
            "skip_reason": skip_reason,
            "diagnostics": diagnostics,
        }
        return listings, stat

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_process_source, source) for source in profile.sources]
        for future in futures:
            try:
                listings, stat = future.result()
                all_listings.extend(listings)
                source_stats.append(stat)
            except Exception as exc:
                print(f"ERROR (worker thread): {exc}", file=sys.stderr)

    # --- Pipeline -------------------------------------------------------------
    from product_search.validators.pipeline import run_pipeline
    passed_listings, rejected_count = run_pipeline(all_listings, profile, qvl)

    passed_by_key: dict[tuple[str, str | None, str | None], int] = {}
    for lst in passed_listings:
        k = _passed_match_key(lst)
        passed_by_key[k] = passed_by_key.get(k, 0) + 1
    for s in source_stats:
        s["passed"] = passed_by_key.get(
            (s["source"], s.get("match_host"), s.get("match_url")), 0
        )

    # ADR-098 fix #4 / ADR-109: compute the dominant rejection reason per source
    # so the classifier can give "URL may be mis-scoped" guidance instead of
    # "loosen your filter" when most rejections are relevance-driven.
    from product_search.validators import ai_filter as _af_mod

    annotate_dominant_rejections(source_stats, list(_af_mod.LAST_RUN_LOG))

    print(
        f"Fetched {len(all_listings)} listing(s). "
        f"Passed: {len(passed_listings)}, Rejected: {rejected_count}.",
        file=sys.stderr,
    )

    # --- Persist (Phase 4) ----------------------------------------------------
    diff_result = None
    # ``csv_path`` is set when storage runs; the Phase 17 alerts evaluator
    # reads it to exclude the just-written CSV when picking the previous run.
    csv_path = None
    if not no_store and passed_listings:
        from product_search.storage.csv_dump import default_csv_path, write_snapshot_csv
        from product_search.storage.db import (
            connect,
            insert_listings,
            query_snapshot_for_date,
            snapshot_dates,
        )
        from product_search.storage.diff import diff_snapshots

        conn = connect(slug)
        try:
            inserted = insert_listings(conn, passed_listings)
            # Compute the diff against the previous distinct snapshot date
            # (if any) so the synthesizer has context.
            dates = snapshot_dates(conn)
            today_str = snapshot_date.isoformat()
            prior_dates = [d for d in dates if d != today_str]
            if prior_dates:
                previous = query_snapshot_for_date(conn, prior_dates[0])
                current = query_snapshot_for_date(conn, today_str)
                diff_result = diff_snapshots(previous, current)

                material = False
                headline = ""
                
                if len(diff_result.new) > 0:
                    material = True
                    headline = f"{len(diff_result.new)} new listings found"
                elif any(ch.pct_change <= -0.05 for ch in diff_result.changed):
                    material = True
                    headline = "Price dropped by >=5%"
                elif previous and current:
                    prev_min = min((lst.unit_price_usd for lst in previous), default=float('inf'))
                    curr_min = min((lst.unit_price_usd for lst in current), default=float('inf'))
                    if curr_min < prev_min:
                        material = True
                        headline = f"New cheapest path: ${curr_min:.2f}"
                
                if material:
                    from product_search.notify import notify_material_change
                    notify_material_change(slug, headline)
        finally:
            conn.close()

        # Per-run CSV (not per-day): timestamp-named so multiple runs on
        # the same date each get their own file rather than overwriting.
        csv_path = default_csv_path(slug, run_started_at)
        write_snapshot_csv(csv_path, passed_listings)

        print(
            f"Stored {inserted} row(s) to SQLite; CSV: {csv_path}",
            file=sys.stderr,
        )

    # --- Synthesize report (Phase 5) ------------------------------------------
    sources_md = _build_sources_searched_md(source_stats, profile)
    # Reset before synth so a synth-failure run still gets a (partial) cost
    # panel that includes ai_filter's spend.
    from product_search.validators import ai_filter as ai_filter_mod

    if not no_report and passed_listings:
        from product_search.synthesizer import (
            SYNTH_MAX_LISTINGS,
            default_report_path,
            synthesize,
            write_report,
        )

        print("Synthesizing report (deterministic, per ADR-096) ...", file=sys.stderr)
        result = synthesize(
            passed_listings,
            diff_result,
            profile,
            snapshot_date=snapshot_date,
        )

        body = result.report_md

        n_passed = len(passed_listings)
        if n_passed > SYNTH_MAX_LISTINGS:
            body += (
                f"\n\n_Showing the top {SYNTH_MAX_LISTINGS} of {n_passed} "
                f"passing listings in the ranking above. The full set is "
                f"persisted to a per-run CSV under `reports/<slug>/data/`._"
            )

        # Build deterministic Run cost panel from ai_filter (if any). Per
        # ADR-096, the synth LLM call is retired, so there's no `synth` row;
        # with the universal_ai scraper retired (Phase 36) there are no
        # per-vendor extraction calls either, leaving only the filter.
        run_calls: list[dict[str, Any]] = []
        if ai_filter_mod.LAST_RUN_USAGE:
            run_calls.append(ai_filter_mod.LAST_RUN_USAGE)
        run_cost_md = _build_run_cost_md(run_calls)

        # --- Alerts evaluator (Phase 17 Part C; modes per ADR-056) -----------
        # drops_below = transition (false→true since the previous run).
        # is_below = state-based: fires while below AND armed, re-arms once the
        # matching cheapest returns to/above the threshold. Audit-trail panel
        # appended to the report so a user inspecting it sees what fired/why.
        alerts_md = ""
        if profile.alerts:
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
                profile.alerts, passed_listings, previous_listings, alerts_state
            )
            # Persist arm/disarm transitions only on stored runs, so an
            # ephemeral (--no-store) run never mutates the user's alert state.
            if csv_path is not None:
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

        report_path = default_report_path(slug, snapshot_date)
        report_body = body + "\n\n" + sources_md + "\n\n" + run_cost_md
        if alerts_md:
            report_body += "\n\n" + alerts_md
        write_report(report_path, report_body)

        # ADR-096: emit the structured JSON sidecar alongside the markdown.
        # The React UI prefers the sidecar and falls back to markdown only
        # for historical reports that pre-date this commit.
        from product_search.synthesizer.report_json import (
            build_json_payload,
            default_json_path,
            write_json_sidecar,
        )

        json_payload = build_json_payload(
            listings=passed_listings,
            profile=profile,
            source_stats=source_stats,
            run_calls=run_calls,
            snapshot_date=snapshot_date,
        )
        json_path = default_json_path(slug, snapshot_date)
        write_json_sidecar(json_path, json_payload)

        print(f"Wrote report: {report_path}", file=sys.stderr)
        print(f"Wrote JSON sidecar: {json_path}", file=sys.stderr)
    elif not no_report:
        # No listings passed the validator pipeline, so there's nothing for
        # the synthesizer to summarise — but the user still wants to see
        # which sites were tried. Write a minimal report with just the
        # sources panel so the day isn't a blank entry.
        from product_search.synthesizer import default_report_path, write_report

        diagnostic_md = _build_filter_diagnostic_md(len(all_listings))
        zero_pass_calls: list[dict[str, Any]] = []
        if ai_filter_mod.LAST_RUN_USAGE:
            zero_pass_calls.append(ai_filter_mod.LAST_RUN_USAGE)
        zero_pass_cost_md = _build_run_cost_md(zero_pass_calls)

        body = (
            f"_No listings passed the validator pipeline for "
            f"{snapshot_date.isoformat()}._\n\n{sources_md}"
        )
        if diagnostic_md:
            body += "\n\n" + diagnostic_md
        body += "\n\n" + zero_pass_cost_md

        report_path = default_report_path(slug, snapshot_date)
        write_report(report_path, body)

        # ADR-096: emit the JSON sidecar for the zero-pass case too so
        # the React UI can render the "no listings, here's why" state
        # natively (an empty listings[] + the populated sources[]).
        from product_search.synthesizer.report_json import (
            build_json_payload,
            default_json_path,
            write_json_sidecar,
        )

        zero_payload = build_json_payload(
            listings=[],
            profile=profile,
            source_stats=source_stats,
            run_calls=zero_pass_calls,
            snapshot_date=snapshot_date,
        )
        zero_json_path = default_json_path(slug, snapshot_date)
        write_json_sidecar(zero_json_path, zero_payload)

        print(f"Wrote sources-only report: {report_path}", file=sys.stderr)
        print(f"Wrote JSON sidecar: {zero_json_path}", file=sys.stderr)

    # --- Output ---------------------------------------------------------------
    print(json.dumps([lst.to_dict() for lst in passed_listings], indent=2))
    sys.exit(0)


def _build_filter_diagnostic_md(total_fetched: int) -> str:
    """Render a 'why did the filter reject everything' diagnostic block.

    Reads :data:`product_search.validators.ai_filter.LAST_RUN_LOG`, which the
    most recent ``ai_filter`` call populated with one row per evaluated
    listing (or a single sentinel row when the LLM call failed entirely).
    Returns "" when there is nothing to show — caller decides whether to
    append it.

    The point of this block is to make a 0-pass run debuggable from the
    committed report alone (which is publicly readable on GitHub raw),
    without needing GitHub Actions auth to download artifacts.
    """
    from product_search.validators import ai_filter as ai_filter_mod

    log_entries = list(ai_filter_mod.LAST_RUN_LOG)
    if not log_entries:
        return ""

    fail_entries = [e for e in log_entries if not e.get("pass")]
    if not fail_entries:
        return ""

    # Sentinel rows have index=-1 — treat those as a hard call-level failure
    # rather than per-listing rejections.
    hard_fail = next((e for e in fail_entries if e.get("index") == -1), None)

    lines = [
        "**AI filter diagnostic.**",
        "",
        f"`ai_filter` evaluated {len(log_entries)} of {total_fetched} fetched "
        f"listing(s) and kept 0. First {min(10, len(fail_entries))} rejection "
        f"reason(s) below; full per-listing log at "
        f"`reports/<slug>/<date>.filter.jsonl` (committed alongside this report).",
        "",
    ]

    if hard_fail is not None:
        lines.append(
            f"_Hard failure_: `{hard_fail.get('reason', '(no reason)')}`"
        )
        raw_excerpt = (ai_filter_mod.LAST_RUN_RAW_RESPONSE or "")[:600]
        if raw_excerpt:
            lines.append("")
            lines.append("Raw LLM response (first 600 chars):")
            lines.append("")
            lines.append("```")
            lines.append(raw_excerpt)
            lines.append("```")
        return "\n".join(lines)

    lines.append("| # | Reason | Title |")
    lines.append("|---|--------|-------|")
    def _cell(value: object, limit: int) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")[:limit]

    for entry in fail_entries[:10]:
        idx = entry.get("index", "?")
        reason = _cell(entry.get("reason", "(no reason)"), 200)
        title = _cell(entry.get("title", ""), 80)
        lines.append(f"| {idx} | {reason} | {title} |")
    return "\n".join(lines)


def _build_run_cost_md(calls: list[dict[str, Any]]) -> str:
    """Render the deterministic 'Run cost' panel for the daily report.

    Each entry in ``calls`` is ``{"step", "provider", "model",
    "input_tokens", "output_tokens"}``. Costs are looked up in
    :data:`product_search.llm.pricing.PRICING` — unknown ``(provider,
    model)`` pairs render as "(unpriced)" rather than $0.0000 so the
    operator can see the gap. Onboarding cost is intentionally NOT
    included here; this panel is the *run* cost only (per the user's
    spec — onboarding cost is shown at the end of the chat instead).
    """
    from product_search.llm.pricing import estimate_cost_usd, format_cost_usd

    if not calls:
        return "**Run cost.**\n\n(no LLM calls were made)"

    lines = ["**Run cost.**", ""]
    lines.append("| Step | Model | Input tokens | Output tokens | Cost (USD) |")
    lines.append("|------|-------|--------------|---------------|------------|")

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

        in_tok = c.get("input_tokens") or 0
        out_tok = c.get("output_tokens") or 0
        lines.append(
            f"| {c.get('step', '?')} | {c.get('provider', '?')}/"
            f"{c.get('model', '?')} | {in_tok:,} | {out_tok:,} | "
            f"{format_cost_usd(cost)} |"
        )

    total_label = format_cost_usd(total_cost)
    if any_unpriced:
        total_label += " (plus unpriced calls)"
    lines.append(f"| **Total** | | | | **{total_label}** |")
    lines.append("")
    lines.append(
        "_Costs are estimates from a hand-maintained price table; actual "
        "billing may differ. Onboarding cost is shown separately at the "
        "end of the onboarding chat._"
    )
    return "\n".join(lines)


def _build_zero_reason_callout(source_stats: list[dict[str, Any]]) -> str:
    """Render the ADR-084 callout explaining each source that returned 0.

    One bullet per non-clean source: a category tag + a plain-English reason and
    whether/how it's fixable. Returns "" when every source produced results, so
    a healthy run carries no extra noise. Uses a ``[!WARNING]`` admonition when
    any source is permanently blocked, else ``[!NOTE]`` — matching the GFM-alert
    style already rendered by the web's ReactMarkdown.
    """
    from product_search.source_reasons import (
        OutcomeCategory,
        classify_source_outcome,
    )

    bullets: list[str] = []
    has_permanent = False
    for s in source_stats:
        outcome = classify_source_outcome(
            fetched=int(s.get("fetched", 0) or 0),
            passed=int(s.get("passed", 0) or 0),
            error=s.get("error"),
            skip_reason=s.get("skip_reason"),
            diagnostics=s.get("diagnostics"),
            known_failure=None,
            dominant_rejection=s.get("dominant_rejection"),
        )
        if outcome.is_clean:
            continue
        if outcome.category is OutcomeCategory.PERMANENT:
            has_permanent = True
        label = s.get("display_source") or s.get("source") or "?"
        msg = str(outcome.message).replace("|", "\\|").replace("\n", " ")
        bullets.append(f"> - **{label}** — _{outcome.label}_: {msg}")

    if not bullets:
        return ""

    head = "> [!WARNING]" if has_permanent else "> [!NOTE]"
    return "\n".join(
        [head, "> **Why some sources returned 0 results:**", *bullets]
    )


def _build_sources_searched_md(
    source_stats: list[dict[str, Any]],
    profile: object,
) -> str:
    """Render the deterministic 'Sources searched' panel for the daily report.

    Independent of the LLM — purely tabulated counts so the synthesizer's
    post-check (which forbids fabricated numbers) sees only data we control.
    """
    # ADR-096: the Status column uses ADR-084's classifier directly, so
    # a source with `fetched: 0` no longer reports `ok`. The classifier
    # returns a short label ("ok" / "no match" / "no results" / "needs
    # work" / "transient" / "blocked") that lines up with the JSON
    # sidecar's `status_label` field — the legacy renderer and the
    # React renderer tell the same story.
    from product_search.source_reasons import classify_source_outcome

    lines = ["**Sources searched.**", ""]

    lines.append("| Source | Status | Fetched | Passed |")
    lines.append("|--------|--------|---------|--------|")
    for s in source_stats:
        outcome = classify_source_outcome(
            fetched=int(s.get("fetched", 0) or 0),
            passed=int(s.get("passed", 0) or 0),
            error=s.get("error"),
            skip_reason=s.get("skip_reason"),
            diagnostics=s.get("diagnostics"),
            known_failure=None,
            dominant_rejection=s.get("dominant_rejection"),
        )
        status = outcome.label
        status = status.replace("|", "\\|").replace("\n", " ").replace("\r", "")
        # ``display_source`` is the human-friendly label (e.g.
        # ``universal_ai (audio46.com)``) when the source loop set one;
        # falls back to the canonical adapter id for older callers.
        label = s.get("display_source") or s["source"]
        lines.append(
            f"| {label} | {status} | {s.get('fetched', 0)} | {s.get('passed', 0)} |"
        )

    # ADR-084: explain every source that returned 0, so a bare "0" becomes a
    # reason the user can act on (transient / blocked / no match / parser gap).
    callout = _build_zero_reason_callout(source_stats)
    if callout:
        lines.append("")
        lines.append(callout)

    pending = getattr(profile, "sources_pending", []) or []
    if pending:
        # Render each pending entry by its most descriptive label: a
        # universal_ai_search demotion shows the vendor host (e.g.
        # ``bestbuy.com``), an unwired adapter shows its id (e.g.
        # ``newegg_search``). Falls back to ``?`` only when nothing useful
        # is available.
        from urllib.parse import urlparse as _urlparse_pending

        def _pending_label(p: object) -> str | None:
            pid = getattr(p, "id", None)
            extra = getattr(p, "model_extra", None) or {}
            url = extra.get("url") if isinstance(extra, dict) else None
            if pid == "universal_ai_search":
                if isinstance(url, str) and url:
                    host = _urlparse_pending(url).netloc.lower()
                    if host.startswith("www."):
                        host = host[4:]
                    return host or url
                # A URL-less universal_ai_search entry is a free-text
                # re-evaluation note, not a wireable vendor — the bare
                # adapter id is meaningless to a reader, so omit it.
                return None
            return pid or "?"

        labels = [
            lbl for p in pending if (lbl := _pending_label(p)) is not None
        ]
        if labels:
            lines.append("")
            lines.append(f"_Pending (not yet wired): {', '.join(labels)}._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def _cmd_diff(slug: str) -> None:
    """Print the diff between the two most recent daily snapshots in SQLite.

    Exits 0 in all non-error cases (including "not enough history yet").
    """
    from product_search.storage.db import (
        connect,
        query_snapshot_for_date,
        snapshot_dates,
    )
    from product_search.storage.diff import diff_snapshots

    conn = connect(slug)
    try:
        dates = snapshot_dates(conn)
        if len(dates) < 2:
            print(
                f"Not enough history for {slug!r}: found {len(dates)} snapshot date(s); "
                "need at least 2.",
                file=sys.stderr,
            )
            sys.exit(0)

        # snapshot_dates returns descending; the most recent is index 0.
        current_date, previous_date = dates[0], dates[1]
        current = query_snapshot_for_date(conn, current_date)
        previous = query_snapshot_for_date(conn, previous_date)
    finally:
        conn.close()

    result = diff_snapshots(previous, current)

    print(f"Diff for {slug!r}: {previous_date} -> {current_date}")
    print(f"  new:     {len(result.new)}")
    print(f"  dropped: {len(result.dropped)}")
    print(f"  changed: {len(result.changed)} (>=5% unit_price_usd move)")

    if result.new:
        print("\nNEW:")
        for lst in result.new:
            print(f"  + ${lst.unit_price_usd:>9.2f}  {lst.title[:80]}  {lst.url}")

    if result.dropped:
        print("\nDROPPED:")
        for lst in result.dropped:
            print(f"  - ${lst.unit_price_usd:>9.2f}  {lst.title[:80]}  {lst.url}")

    if result.changed:
        print("\nPRICE-CHANGED:")
        for ch in result.changed:
            arrow = "UP  " if ch.pct_change > 0 else "DOWN"
            print(
                f"  {arrow} ${ch.old_price_usd:>9.2f} -> ${ch.new_price_usd:>9.2f} "
                f"({ch.pct_change * 100:+.1f}%)  {ch.title[:60]}  {ch.url}"
            )

    sys.exit(0)


# ---------------------------------------------------------------------------
# scheduler-tick
# ---------------------------------------------------------------------------


# The heartbeat workflow (.github/workflows/search-scheduled.yml) ticks every
# 15 min. A recurring cron is "due" if it has an occurrence in the look-back
# window (now - TICK_WINDOW_MINUTES, now]. Consecutive on-time ticks have
# non-overlapping windows, so a firing is counted once. If GitHub delays or
# skips a high-frequency cron under load, a firing lost in the gap is missed
# — accepted and documented in ADR-050.
TICK_WINDOW_MINUTES = 15

_CRON_PART_RE = re.compile(r"(\*|\d+)(?:-(\d+))?(?:/(\d+))?")
_SCHEDULE_BLOCK_RE = re.compile(
    r"^schedule:[ \t]*\r?\n(?:[ \t]+[^\r\n]*\r?\n?)+", re.MULTILINE
)


def _expand_cron_field(field: str, lo: int, hi: int) -> set[int] | None:
    """Expand one cron field into the set of values it matches within
    ``[lo, hi]``. Supports ``*``, ``*/n``, ``a-b``, ``a-b/n``, literals and
    comma lists. Returns ``None`` on any unsupported pattern — the scheduler
    then treats the cron as non-matching (conservative: never fire on a cron
    we cannot parse rather than fire unexpectedly)."""
    out: set[int] = set()
    for part in field.split(","):
        m = _CRON_PART_RE.fullmatch(part)
        if not m:
            return None
        base, end, step_s = m.group(1), m.group(2), m.group(3)
        step = int(step_s) if step_s else 1
        if step < 1:
            return None
        if base == "*":
            start, stop = lo, hi
        else:
            start = int(base)
            # `a` -> just a; `a-b` -> a..b; `a/n` -> a..hi step n.
            stop = int(end) if end is not None else (hi if step_s else start)
        if start < lo or stop > hi or start > stop:
            return None
        out.update(range(start, stop + 1, step))
    return out or None


def _cron_fires_at(cron_expr: str, dt: datetime) -> bool:
    """True iff a 5-field cron fires at the given UTC minute-resolution
    instant. Implements the standard Vixie day-of-month / day-of-week OR
    rule: when both are restricted, either matching is enough."""
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False
    minute = _expand_cron_field(parts[0], 0, 59)
    hour = _expand_cron_field(parts[1], 0, 23)
    dom = _expand_cron_field(parts[2], 1, 31)
    month = _expand_cron_field(parts[3], 1, 12)
    # cron day-of-week: Sun=0..Sat=6 (7 also means Sun, normalised below).
    dow = _expand_cron_field(parts[4], 0, 7)
    if minute is None or hour is None or dom is None or month is None or dow is None:
        return False
    dow = {0 if d == 7 else d for d in dow}

    if dt.minute not in minute or dt.hour not in hour or dt.month not in month:
        return False
    dom_restricted = parts[2] != "*"
    dow_restricted = parts[4] != "*"
    dom_ok = dt.day in dom
    # isoweekday(): Mon=1..Sun=7 -> %7 -> Mon=1..Sat=6, Sun=0 (cron's scheme).
    dow_ok = (dt.isoweekday() % 7) in dow
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def _cron_due(cron_expr: str, window_start: datetime, now: datetime) -> bool:
    """True iff the cron has at least one firing minute ``t`` with
    ``window_start < t <= now``."""
    probe = now.replace(second=0, microsecond=0)
    while probe > window_start:
        if _cron_fires_at(cron_expr, probe):
            return True
        probe -= timedelta(minutes=1)
    return False


def _strip_schedule_block(yaml_text: str) -> str:
    """Remove the whole ``schedule:`` block (and one trailing blank line)
    from a profile's raw YAML. Mirror of ``applyScheduleToYaml(text, null)``
    in ``web/lib/schedule.ts`` so a one-time run self-clears after firing."""
    return re.sub(
        _SCHEDULE_BLOCK_RE.pattern + r"\r?\n?",
        "",
        yaml_text,
        count=1,
        flags=re.MULTILINE,
    )


def _cmd_scheduler_tick() -> None:
    """Walk products/, decide which profiles are due now, and run search.

    Recurring (``cron``) profiles fire when the cron has an occurrence in
    the look-back window. One-time (``run_at``) profiles fire when their
    instant is in the past; the schedule block is then stripped from the
    profile so the run never repeats (the workflow commits the edit)."""
    import subprocess
    from pathlib import Path

    from product_search.profile import _repo_root, load_profile

    now = datetime.now(tz=UTC).replace(second=0, microsecond=0)
    window_start = now - timedelta(minutes=TICK_WINDOW_MINUTES)

    repo_root = _repo_root()
    products_dir = repo_root / "products"
    if not products_dir.is_dir():
        products_dir = Path.cwd().parent / "products"

    if not products_dir.exists() or not products_dir.is_dir():
        print(f"ERROR: {products_dir} not found.", file=sys.stderr)
        sys.exit(1)

    print(
        f"[{now.isoformat()}] scheduler-tick running "
        f"(window {window_start.isoformat()} .. {now.isoformat()})",
        file=sys.stderr,
    )

    failures = 0
    for path in sorted(products_dir.iterdir()):
        if not path.is_dir() or path.name.startswith("_"):
            continue

        slug = path.name
        try:
            from product_search.profile_v2 import peek_schema_version
            if peek_schema_version(slug) == 2:
                from product_search.profile_v2 import load_profile_v2
                profile = load_profile_v2(slug)
            else:
                profile = load_profile(slug)
        except Exception as exc:
            print(f"Skipping {slug} (invalid profile): {exc}", file=sys.stderr)
            continue

        sched = profile.schedule
        if sched is None:
            print(
                f"[{now.isoformat()}] Skipping {slug} (no schedule — run-now only)",
                file=sys.stderr,
            )
            continue

        one_time = sched.run_at is not None
        if one_time:
            assert sched.run_at is not None
            due = sched.run_at <= now
            reason = f"run_at={sched.run_at.isoformat()}"
        else:
            assert sched.cron is not None
            due = _cron_due(sched.cron, window_start, now)
            reason = f"cron={sched.cron!r}"

        if not due:
            print(
                f"[{now.isoformat()}] Skipping {slug} (not due; {reason})",
                file=sys.stderr,
            )
            continue

        print(
            f"[{now.isoformat()}] => Running search for {slug} ({reason})",
            file=sys.stderr,
        )
        cmd = [sys.executable, "-m", "product_search.cli", "search", slug]
        # Subprocess-isolated: _cmd_search calls sys.exit and could leak
        # state if called in-process.
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(
                f"ERROR: run for {slug} failed with code {result.returncode}",
                file=sys.stderr,
            )
            failures += 1

        if one_time:
            # A one-time job is attempted exactly once. Strip the schedule
            # block regardless of the run's exit code so a broken profile
            # cannot retry every tick forever (ADR-050). The workflow's
            # final `git add -A && commit && push` persists the removal.
            profile_file = path / "profile.yaml"
            try:
                original = profile_file.read_text(encoding="utf-8")
                cleared = _strip_schedule_block(original)
                if cleared != original:
                    profile_file.write_text(cleared, encoding="utf-8")
                    print(
                        f"[{now.isoformat()}] Cleared one-time schedule for {slug}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"[{now.isoformat()}] WARNING: no schedule block found "
                        f"to clear for {slug}",
                        file=sys.stderr,
                    )
            except OSError as exc:
                print(
                    f"[{now.isoformat()}] WARNING: failed to clear one-time "
                    f"schedule for {slug}: {exc}",
                    file=sys.stderr,
                )

    if failures > 0:
        print(
            f"[{now.isoformat()}] scheduler-tick completed with "
            f"{failures} failure(s).",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"[{now.isoformat()}] scheduler-tick completed successfully.",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
