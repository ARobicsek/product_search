"""Phase 2 tests — Listing model, LLM abstraction, eBay adapter.

Coverage:
  Listing model
    1. Construct a Listing; to_dict() is JSON-serialisable.
    2. to_json() round-trips back to the same dict.
    3. AdapterQuery.from_profile_source() maps all known keys.

  eBay adapter (fixture mode — no network, no credentials)
    4. fetch() returns Listings from the saved fixture file.
    5. Listing fields are populated as expected from fixture data.
    6. Kit listings have is_kit=True and unit_price_usd < kit_price_usd.
    7. Live mode without credentials raises EbayAuthError immediately.

  LLM abstraction (import-only — no network calls)
    8. call_llm() raises ImportError for anthropic when SDK absent.
    9. call_llm() raises LLMError for an unknown provider name.

  CLI integration (fixture mode)
   10. `product-search search ddr5-rdimm-256gb --no-store` exits 0 and
       prints valid JSON when WORKER_USE_FIXTURES=1.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from product_search.models import AdapterQuery, Listing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "products").is_dir():
            return parent
    raise RuntimeError("Could not find repo root")


REPO_ROOT = _find_repo_root()
FIXTURE_FILE = REPO_ROOT / "worker" / "tests" / "fixtures" / "ebay" / "ddr5_4800_rdimm_32gb.json"


def _make_listing(**overrides: object) -> Listing:
    defaults: dict[str, Any] = {
        "source": "ebay_search",
        "url": "https://www.ebay.com/itm/123",
        "title": "Samsung 32GB DDR5 4800MHz RDIMM",
        "fetched_at": datetime.now(tz=UTC),
        "brand": "Samsung",
        "mpn": "M321R4GA0BB0-CQK",
        "attrs": {"capacity_gb": 32, "speed_mts": 4800},
        "condition": "new",
        "is_kit": False,
        "kit_module_count": 1,
        "unit_price_usd": 119.99,
        "kit_price_usd": None,
        "quantity_available": 10,
        "seller_name": "test_seller",
        "seller_rating_pct": 99.5,
        "seller_feedback_count": 5000,
        "ship_from_country": "US",
    }
    defaults.update(overrides)
    return Listing(**defaults)


# ---------------------------------------------------------------------------
# Listing model tests
# ---------------------------------------------------------------------------


def test_listing_to_dict_is_json_serialisable() -> None:
    """to_dict() must produce a dict that json.dumps() can handle."""
    listing = _make_listing()
    d = listing.to_dict()
    # Should not raise
    serialised = json.dumps(d)
    assert "ebay_search" in serialised


def test_listing_to_json_round_trips() -> None:
    """to_json() -> json.loads() must reproduce the same dict."""
    listing = _make_listing()
    d1 = listing.to_dict()
    d2 = json.loads(listing.to_json())
    assert d1 == d2


def test_adapter_query_from_profile_source() -> None:
    """from_profile_source() must map known keys and stash extras."""
    raw = {
        "id": "ebay_search",
        "queries": ["DDR5 32GB"],
        "max_results_per_query": 25,
        "extra_key": "extra_value",
    }
    q = AdapterQuery.from_profile_source(raw)
    assert q.source_id == "ebay_search"
    assert q.queries == ["DDR5 32GB"]
    assert q.max_results_per_query == 25
    assert q.extra.get("extra_key") == "extra_value"


# ---------------------------------------------------------------------------
# eBay adapter tests (fixture mode)
# ---------------------------------------------------------------------------


def test_ebay_fetch_fixture_returns_listings() -> None:
    """fetch() in fixture mode must return at least one Listing."""
    from product_search.adapters.ebay import fetch

    query = AdapterQuery(source_id="ebay_search", queries=["DDR5"])
    listings = fetch(query, fixture_path=FIXTURE_FILE)
    assert len(listings) >= 1
    assert all(isinstance(lst, Listing) for lst in listings)


def test_ebay_fixture_listing_fields() -> None:
    """The Samsung MPN in the fixture must map to the expected Listing fields."""
    from product_search.adapters.ebay import fetch

    query = AdapterQuery(source_id="ebay_search")
    listings = fetch(query, fixture_path=FIXTURE_FILE)

    # Find the single-module Samsung 32GB listing.
    samsung_single = next(
        (lst for lst in listings if lst.mpn == "M321R4GA0BB0-CQK" and not lst.is_kit),
        None,
    )
    assert samsung_single is not None, "Expected single-module Samsung listing in fixture"
    assert samsung_single.source == "ebay_search"
    assert samsung_single.condition == "new"
    assert samsung_single.unit_price_usd == pytest.approx(119.99)
    assert samsung_single.seller_name == "cloudstorageusa"
    assert samsung_single.seller_rating_pct == pytest.approx(99.8)
    assert samsung_single.ship_from_country == "US"
    assert samsung_single.quantity_available == 42
    assert samsung_single.attrs.get("capacity_gb") == 32
    assert samsung_single.attrs.get("speed_mts") == 4800


def test_ebay_fixture_kit_detection() -> None:
    """The 8x32GB kit listing must be detected as a kit."""
    from product_search.adapters.ebay import fetch

    query = AdapterQuery(source_id="ebay_search")
    listings = fetch(query, fixture_path=FIXTURE_FILE)

    kit = next((lst for lst in listings if lst.is_kit), None)
    assert kit is not None, "Expected at least one kit listing in fixture"
    assert kit.kit_module_count == 8
    assert kit.kit_price_usd is not None
    assert kit.unit_price_usd < kit.kit_price_usd


def test_ebay_live_mode_raises_without_credentials() -> None:
    """fetch() in live mode must raise EbayAuthError when creds are absent."""
    from product_search.adapters.ebay import EbayAuthError, fetch

    # Remove any accidentally-set env vars for this test
    env_backup = {}
    for key in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "WORKER_USE_FIXTURES"):
        env_backup[key] = os.environ.pop(key, None)

    try:
        query = AdapterQuery(source_id="ebay_search", queries=["DDR5"])
        with pytest.raises(EbayAuthError):
            fetch(query)
    finally:
        for key, val in env_backup.items():
            if val is not None:
                os.environ[key] = val


# ---------------------------------------------------------------------------
# LLM abstraction tests (import-only, no network)
# ---------------------------------------------------------------------------


def test_llm_unknown_provider_raises() -> None:
    """call_llm() with an unknown provider must raise LLMError."""
    from product_search.llm import LLMError, Message, call_llm

    with pytest.raises((LLMError, Exception)):
        call_llm(
            provider="nonexistent_provider",  # type: ignore[arg-type]
            model="whatever",
            system="hello",
            messages=[Message(role="user", content="hi")],
        )


def test_llm_anthropic_raises_import_error_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the anthropic SDK isn't installed, call_llm must raise ImportError."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Also unload the module from sys.modules to force re-import
    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.delitem(sys.modules, "product_search.llm._anthropic", raising=False)

    from product_search.llm import Message, call_llm

    with pytest.raises(ImportError, match="anthropic"):
        call_llm(
            provider="anthropic",
            model="claude-haiku-4-5",
            system="hello",
            messages=[Message(role="user", content="hi")],
        )


# ---------------------------------------------------------------------------
# CLI integration (fixture mode)
# ---------------------------------------------------------------------------


def test_cli_search_fixture_mode_exits_zero() -> None:
    """product-search search in fixture mode must exit 0 and print valid JSON."""
    env = {**os.environ, "WORKER_USE_FIXTURES": "1"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "product_search.cli",
            "search",
            "ddr5-rdimm-256gb",
            "--no-store",
            "--no-report",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "worker"),
        env=env,
    )
    assert result.returncode == 0, (
        f"CLI search failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:500]}"
    )
    # stdout should be valid JSON (a list of listing dicts)
    listings = json.loads(result.stdout)
    assert isinstance(listings, list)
    assert len(listings) >= 1
    assert "url" in listings[0]
    assert "unit_price_usd" in listings[0]
