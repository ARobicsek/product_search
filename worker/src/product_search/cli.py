"""CLI entry point for the product-search worker.

Commands are added per-phase. This file is the stable entry point;
sub-commands live in their respective modules.

Phase 1: validate <slug>
Phase 2: llm-ping <provider> <model>
         search <slug>
Phase 4: diff <slug>
Phase 5: search <slug> writes reports/<slug>/<date>.md via the synthesizer
"""

import argparse
import sys


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
        help="Do not write results to SQLite or to the daily CSV dump",
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

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "validate":
        _cmd_validate(args.slug)

    elif args.command == "llm-ping":
        _cmd_llm_ping(args.provider, args.model)

    elif args.command == "search":
        _cmd_search(
            args.slug,
            no_validate=args.no_validate,
            no_store=args.no_store,
            no_report=args.no_report,
        )

    elif args.command == "diff":
        _cmd_diff(args.slug)


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

    from product_search.adapters.ebay import EbayAuthError, fetch
    from product_search.models import AdapterQuery
    from product_search.profile import load_profile, load_qvl

    # --- Load profile ---------------------------------------------------------
    if not no_validate:
        from pydantic import ValidationError

        try:
            profile = load_profile(slug)
            qvl = load_qvl(slug)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        except ValidationError as exc:
            print(f"INVALID profile for {slug!r}:", file=sys.stderr)
            print(exc, file=sys.stderr)
            sys.exit(1)
    else:
        profile = load_profile(slug)
        qvl = load_qvl(slug)

    use_fixtures = os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes")
    mode = "fixture" if use_fixtures else "live"
    print(f"Searching {slug!r} via eBay [{mode} mode] ...", file=sys.stderr)

    # --- Run adapters ---------------------------------------------------------
    all_listings = []
    for source in profile.sources:
        # Phase 2 implements only ebay_search; others are stubs.
        if source.id == "ebay_search":
            query = AdapterQuery.from_profile_source(source.model_dump())
            try:
                listings = fetch(query)
            except EbayAuthError as exc:
                print(f"ERROR (eBay auth): {exc}", file=sys.stderr)
                print(
                    "Tip: set WORKER_USE_FIXTURES=1 to use saved fixtures "
                    "while waiting for eBay API credentials.",
                    file=sys.stderr,
                )
                sys.exit(1)
            except Exception as exc:
                print(f"ERROR (eBay fetch): {exc}", file=sys.stderr)
                sys.exit(1)
            all_listings.extend(listings)

    # --- Pipeline -------------------------------------------------------------
    from product_search.validators.pipeline import run_pipeline
    passed_listings, rejected_count = run_pipeline(all_listings, profile, qvl)

    print(
        f"Fetched {len(all_listings)} listing(s). "
        f"Passed: {len(passed_listings)}, Rejected: {rejected_count}.",
        file=sys.stderr,
    )

    # --- Persist (Phase 4) ----------------------------------------------------
    snapshot_date = datetime.now(tz=UTC).date()
    diff_result = None
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
        finally:
            conn.close()

        csv_path = default_csv_path(slug, snapshot_date)
        write_snapshot_csv(csv_path, passed_listings)

        print(
            f"Stored {inserted} row(s) to SQLite; CSV: {csv_path}",
            file=sys.stderr,
        )

    # --- Synthesize report (Phase 5) ------------------------------------------
    if not no_report and passed_listings:
        from product_search.config import synth_config
        from product_search.synthesizer import (
            PostCheckError,
            default_report_path,
            synthesize,
            write_report,
        )

        cfg = synth_config()
        print(
            f"Synthesizing report via {cfg.provider}/{cfg.model} ...",
            file=sys.stderr,
        )
        try:
            result = synthesize(
                passed_listings,
                diff_result,
                profile,
                provider=cfg.provider,
                model=cfg.model,
                snapshot_date=snapshot_date,
            )
        except PostCheckError as exc:
            print(f"ERROR (synth post-check): {exc}", file=sys.stderr)
            sys.exit(1)

        report_path = default_report_path(slug, snapshot_date)
        write_report(report_path, result.report_md)
        print(
            f"Wrote report: {report_path}  "
            f"(in={result.input_tokens}, out={result.output_tokens})",
            file=sys.stderr,
        )

    # --- Output ---------------------------------------------------------------
    print(json.dumps([lst.to_dict() for lst in passed_listings], indent=2))
    sys.exit(0)


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


if __name__ == "__main__":
    main()
