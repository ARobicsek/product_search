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

    # Phase 1: validate sub-command placeholder
    _validate = subparsers.add_parser("validate", help="Validate a product profile (Phase 1)")
    _validate.add_argument("slug", help="Product slug (e.g. ddr5-rdimm-256gb)")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "validate":
        print(f"[Phase 1 — not yet implemented] validate {args.slug}")
        sys.exit(0)


if __name__ == "__main__":
    main()
