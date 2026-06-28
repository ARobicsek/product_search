"""Phase 35 — verify diff + alerts + push are wired into the v2 run pipeline.

These tests exercise the run_v2 pipeline's alerts integration (REBUILD_PLAN §5
steps 7/9) without network calls: recall is injected, notify is mocked.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from product_search.models import Listing
from product_search.profile import NewVendorCarriesAlert, PriceBelowAlert


def _mk_listing(
    *,
    url: str = "https://example.com/p/123",
    unit_price_usd: float = 100.0,
    condition: str = "new",
    source: str = "serper_shopping",
    seller_name: str = "someone",
) -> Listing:
    return Listing(
        source=source,
        url=url,
        title="Test listing",
        fetched_at=datetime(2026, 5, 31, 12, 0, 0, tzinfo=UTC),
        brand="Acme",
        mpn="ACME-1",
        attrs={},
        condition=condition,
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=unit_price_usd,
        kit_price_usd=None,
        quantity_available=None,
        seller_name=seller_name,
        seller_rating_pct=None,
        seller_feedback_count=None,
        ship_from_country=None,
    )


@pytest.fixture
def v2_profile_dir(tmp_path: Path) -> Path:
    """Create a minimal v2 profile fixture with alerts."""
    slug = "test-v2-alerts"
    profile_dir = tmp_path / "products" / slug
    profile_dir.mkdir(parents=True)
    profile_yaml = profile_dir / "profile.yaml"
    profile_yaml.write_text(
        """
schema_version: 2
slug: test-v2-alerts
display_name: Test V2 Alerts Product
product_type: electronics
target:
  unit: unit
  amount: 1
queries:
  - "test product"
match:
  aliases: ["test 123"]
  variant_strict: false
filters:
  condition_in: ["new"]
sources:
  serper: { enabled: true }
alerts:
  - kind: price_below
    threshold_usd: 200
    mode: is_below
  - kind: new_vendor_carries
""".strip(),
        encoding="utf-8",
    )
    return tmp_path


def test_v2_profile_loads_typed_alerts(v2_profile_dir: Path) -> None:
    """Confirm profile_v2 loads typed alert rules (not raw dicts)."""
    os.environ["PRODUCT_SEARCH_PRODUCTS_DIR"] = str(v2_profile_dir / "products")
    try:
        from product_search.profile_v2 import load_profile_v2
        profile = load_profile_v2("test-v2-alerts")
        assert len(profile.alerts) == 2
        assert isinstance(profile.alerts[0], PriceBelowAlert)
        assert isinstance(profile.alerts[1], NewVendorCarriesAlert)
    finally:
        os.environ.pop("PRODUCT_SEARCH_PRODUCTS_DIR", None)


def test_v2_run_evaluates_alerts_and_calls_notify(
    v2_profile_dir: Path, tmp_path: Path
) -> None:
    """End-to-end: v2 run with alerts -> evaluates -> calls notify."""
    os.environ["PRODUCT_SEARCH_PRODUCTS_DIR"] = str(v2_profile_dir / "products")
    os.environ["PRODUCT_SEARCH_REPORTS_DIR"] = str(tmp_path / "reports")
    try:
        from product_search.run_v2 import run_v2

        listings = [
            _mk_listing(url="https://amazon.com/dp/X", unit_price_usd=150.0),
            _mk_listing(url="https://bhphotovideo.com/c/product/X", unit_price_usd=180.0),
        ]

        with patch("product_search.notify.notify_material_change", return_value=True) as mock_notify:
            with patch("product_search.storage.db._repo_root", return_value=tmp_path):
                with patch("product_search.validators.ai_filter.ai_filter", side_effect=lambda lst, *_: lst):
                    run_v2(
                        "test-v2-alerts",
                        recall_fn=lambda _profile: listings,
                    )

        # On the first run (no previous CSV), both alerts should fire:
        # - price_below: $150 < $200 threshold -> fires
        # - new_vendor_carries: both amazon.com and bhphotovideo.com are new -> fires for each
        assert mock_notify.call_count >= 2  # at least price_below + new_vendor_carries
    finally:
        os.environ.pop("PRODUCT_SEARCH_PRODUCTS_DIR", None)
        os.environ.pop("PRODUCT_SEARCH_REPORTS_DIR", None)
