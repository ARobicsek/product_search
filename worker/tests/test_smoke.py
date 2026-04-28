"""Smoke tests for the worker package skeleton.

Phase 0 only verifies the package imports correctly and the CLI entry
point is reachable. Real tests are added per-phase starting in Phase 1.
"""

import importlib


def test_package_importable() -> None:
    """product_search must be importable without errors."""
    mod = importlib.import_module("product_search")
    assert hasattr(mod, "__version__")


def test_cli_importable() -> None:
    """CLI module must be importable and expose main()."""
    cli = importlib.import_module("product_search.cli")
    assert callable(cli.main)
