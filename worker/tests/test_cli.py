"""Tests for ``product_search.cli`` helpers that don't need a full CLI run.

Scoped to ``_passed_match_key`` (the per-listing key for source-stats joins)
and ``_cmd_probe_url`` (Phase 15's diagnostic command).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from product_search.adapters import universal_ai
from product_search.cli import _cmd_probe_url, _passed_match_key
from product_search.models import Listing

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "universal_ai"


def _make_listing(source: str, attrs: dict | None = None) -> Listing:
    """Build a minimally-populated Listing for key tests."""
    return Listing(
        source=source,
        url="https://example.com/p/x",
        title="x",
        fetched_at=datetime.now(tz=UTC),
        brand=None,
        mpn=None,
        attrs=attrs or {},
        condition="new",
        is_kit=False,
        kit_module_count=1,
        unit_price_usd=1.0,
        kit_price_usd=None,
        quantity_available=None,
        seller_name="x",
        seller_rating_pct=None,
        seller_feedback_count=None,
        ship_from_country=None,
    )


def test_passed_match_key_non_universal_keys_by_source_only() -> None:
    lst = _make_listing("ebay_search")
    assert _passed_match_key(lst) == ("ebay_search", None)


def test_passed_match_key_universal_keys_by_vendor_host() -> None:
    lst = _make_listing("universal_ai_search", {"vendor_host": "backmarket.com"})
    assert _passed_match_key(lst) == ("universal_ai_search", "backmarket.com")


def test_passed_match_key_strips_www_prefix() -> None:
    # The cli's source_stats row stores the host already-stripped of "www.";
    # the listing-side key must match that shape so the join works.
    lst = _make_listing("universal_ai_search", {"vendor_host": "www.bhphotovideo.com"})
    assert _passed_match_key(lst) == ("universal_ai_search", "bhphotovideo.com")


def test_passed_match_key_lowercases_host() -> None:
    lst = _make_listing("universal_ai_search", {"vendor_host": "BackMarket.com"})
    assert _passed_match_key(lst) == ("universal_ai_search", "backmarket.com")


def test_passed_match_key_universal_without_host_falls_back_to_none() -> None:
    # Older universal_ai listings that pre-date the vendor_host attr would
    # group under the "no host" bucket; this is acceptable since the source
    # row in that case would also have match_host=None.
    lst = _make_listing("universal_ai_search", {})
    assert _passed_match_key(lst) == ("universal_ai_search", None)


def test_passed_match_key_disambiguates_two_universal_vendors() -> None:
    # The actual bug repro: two listings, two vendors, two distinct keys.
    a = _make_listing("universal_ai_search", {"vendor_host": "backmarket.com"})
    b = _make_listing("universal_ai_search", {"vendor_host": "bhphotovideo.com"})
    assert _passed_match_key(a) != _passed_match_key(b)


# --- probe-url --------------------------------------------------------------


def test_probe_url_exits_zero_on_jsonld_fixture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """JSON-LD fixture → exit 0, report shows 2 JSON-LD listings."""
    html = (FIXTURE_DIR / "shopify_jsonld.html").read_text(encoding="utf-8")
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0: (html, 200, "stub"),
    )

    with pytest.raises(SystemExit) as exc:
        _cmd_probe_url("https://shop.synthstore.example.com/", render=False)
    assert exc.value.code == 0

    captured = capsys.readouterr()
    assert "JSON-LD listings:  2" in captured.out
    assert "Synthbose QuietComfort 700" in captured.out


def test_probe_url_exits_nonzero_on_no_candidates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A page with neither JSON-LD nor anchor candidates → exit 1."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0: (
            "<html><body><p>Just a Cloudflare challenge.</p></body></html>",
            200, "stub",
        ),
    )

    with pytest.raises(SystemExit) as exc:
        _cmd_probe_url("https://blocked.example.com/", render=False)
    assert exc.value.code == 1


def test_probe_url_render_requires_alterlab_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--render without ALTERLAB_API_KEY must fail loudly (exit 2)."""
    monkeypatch.delenv("ALTERLAB_API_KEY", raising=False)

    with pytest.raises(SystemExit) as exc:
        _cmd_probe_url("https://example.com/", render=True)
    assert exc.value.code == 2

    captured = capsys.readouterr()
    assert "--render requires ALTERLAB_API_KEY" in captured.err


def test_probe_url_render_errors_when_alterlab_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If --render is set and the fetch falls back to curl_cffi (AlterLab
    failed), the command must error — otherwise the user can't tell whether
    the rendered path worked."""
    monkeypatch.setenv("ALTERLAB_API_KEY", "test-key")
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0: ("<html></html>", 200, "curl_cffi"),
    )

    with pytest.raises(SystemExit) as exc:
        _cmd_probe_url("https://example.com/", render=True)
    assert exc.value.code == 1
