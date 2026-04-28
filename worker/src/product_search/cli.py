"""CLI entry point for the product-search worker.

Commands are added per-phase. This file is the stable entry point;
sub-commands live in their respective modules.
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="product-search",
        description="Product Search Worker CLI",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # Phase 1: validate sub-command
    validate_parser = subparsers.add_parser(
        "validate", help="Validate a product profile against the schema"
    )
    validate_parser.add_argument("slug", help="Product slug (e.g. ddr5-rdimm-256gb)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "validate":
        _cmd_validate(args.slug)


def _cmd_validate(slug: str) -> None:
    """Load and validate the profile + QVL for *slug*.

    Exits 0 on success, 1 on validation failure, 2 on file-not-found.
    """
    from pydantic import ValidationError

    from product_search.profile import load_profile, load_qvl

    # --- Profile ---
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

    # --- QVL (optional — warn but don't fail if missing) ---
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


if __name__ == "__main__":
    main()
