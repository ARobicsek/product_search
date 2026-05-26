"""Tests for ``product_search.cli`` helpers that don't need a full CLI run.

Scoped to ``_passed_match_key`` (the per-listing key for source-stats joins)
and ``_cmd_probe_url`` (Phase 15's diagnostic command).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from product_search.adapters import universal_ai
from product_search.cli import (
    _build_zero_reason_callout,
    _cmd_probe_url,
    _passed_match_key,
)
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
    assert _passed_match_key(lst) == ("ebay_search", None, None)


def test_passed_match_key_universal_keys_by_vendor_host() -> None:
    lst = _make_listing("universal_ai_search", {"vendor_host": "backmarket.com"})
    assert _passed_match_key(lst) == ("universal_ai_search", "backmarket.com", None)


def test_passed_match_key_strips_www_prefix() -> None:
    # The cli's source_stats row stores the host already-stripped of "www.";
    # the listing-side key must match that shape so the join works.
    lst = _make_listing("universal_ai_search", {"vendor_host": "www.bhphotovideo.com"})
    assert _passed_match_key(lst) == ("universal_ai_search", "bhphotovideo.com", None)


def test_passed_match_key_lowercases_host() -> None:
    lst = _make_listing("universal_ai_search", {"vendor_host": "BackMarket.com"})
    assert _passed_match_key(lst) == ("universal_ai_search", "backmarket.com", None)


def test_passed_match_key_universal_without_host_falls_back_to_none() -> None:
    # Older universal_ai listings that pre-date the vendor_host attr would
    # group under the "no host" bucket; this is acceptable since the source
    # row in that case would also have match_host=None.
    lst = _make_listing("universal_ai_search", {})
    assert _passed_match_key(lst) == ("universal_ai_search", None, None)


def test_passed_match_key_disambiguates_two_universal_vendors() -> None:
    # The actual bug repro: two listings, two vendors, two distinct keys.
    a = _make_listing("universal_ai_search", {"vendor_host": "backmarket.com"})
    b = _make_listing("universal_ai_search", {"vendor_host": "bhphotovideo.com"})
    assert _passed_match_key(a) != _passed_match_key(b)


def test_passed_match_key_carries_source_url_for_same_host_disambiguation() -> None:
    # Phase 27 / D2: multiple URLs on the SAME host (e.g. four Best Buy detail
    # URLs all share vendor_host = bestbuy.com). The cli stamps `source_url`
    # into attrs at fetch-emit time so the per-source-row passed count can be
    # attributed by URL rather than collapsing to the host total.
    a = _make_listing("universal_ai_search", {
        "vendor_host": "bestbuy.com",
        "source_url": "https://www.bestbuy.com/site/sony-wh-1000xm5-black/6505794.p?skuId=6505794",
    })
    b = _make_listing("universal_ai_search", {
        "vendor_host": "bestbuy.com",
        "source_url": "https://www.bestbuy.com/site/sony-wh-1000xm5-silver/6505795.p?skuId=6505795",
    })
    assert _passed_match_key(a) != _passed_match_key(b)
    assert _passed_match_key(a)[2] == (
        "https://www.bestbuy.com/site/sony-wh-1000xm5-black/6505794.p?skuId=6505794"
    )


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


def test_probe_url_with_alterlab_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """When _cmd_probe_url is called with custom country, min_tier, or
    wait_condition, they are built into alterlab_options and passed to
    _fetch_html (ADR-071)."""
    monkeypatch.setenv("ALTERLAB_API_KEY", "test-key-cli")

    html = (FIXTURE_DIR / "shopify_jsonld.html").read_text(encoding="utf-8")
    captured_opts: list[dict[str, Any] | None] = []

    def _mock_fetch(
        url: str, timeout: float = 20.0, alterlab_options: dict[str, Any] | None = None
    ) -> tuple[str, int, str]:
        captured_opts.append(alterlab_options)
        return (html, 200, "alterlab")

    monkeypatch.setattr(universal_ai, "_fetch_html", _mock_fetch)

    # Calling with all three custom options
    with pytest.raises(SystemExit) as exc:
        _cmd_probe_url(
            "https://example.com/",
            render=False,
            country="gb",
            min_tier=2,
            wait_condition="networkidle",
        )
    assert exc.value.code == 0

    assert len(captured_opts) == 1
    opts = captured_opts[0]
    assert opts is not None
    assert opts["country"] == "gb"
    assert opts["min_tier"] == 2
    assert opts["wait_condition"] == "networkidle"
    assert opts["render_js"] is True


def test_probe_url_parser_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The argument parser correctly parses --country, --min-tier, and
    --wait-condition and forwards them to _cmd_probe_url (ADR-071)."""
    import sys

    from product_search import cli

    captured: list[dict[str, Any]] = []

    def _mock_cmd_probe_url(
        url: str,
        *,
        render: bool,
        save_body: str | None = None,
        detail: bool = False,
        country: str | None = None,
        min_tier: int | None = None,
        wait_condition: str | None = None,
    ) -> None:
        captured.append({
            "url": url,
            "render": render,
            "save_body": save_body,
            "detail": detail,
            "country": country,
            "min_tier": min_tier,
            "wait_condition": wait_condition,
        })

    monkeypatch.setattr(cli, "_cmd_probe_url", _mock_cmd_probe_url)

    # Simulate: python -m product_search.cli probe-url "https://example.com"
    #   --country us --min-tier 3 --wait-condition networkidle --render --detail
    monkeypatch.setattr(sys, "argv", [
        "cli.py", "probe-url", "https://example.com",
        "--country", "us",
        "--min-tier", "3",
        "--wait-condition", "networkidle",
        "--render",
        "--detail",
    ])

    cli.main()

    assert len(captured) == 1
    c = captured[0]
    assert c["url"] == "https://example.com"
    assert c["render"] is True
    assert c["detail"] is True
    assert c["country"] == "us"
    assert c["min_tier"] == 3
    assert c["wait_condition"] == "networkidle"


# --- ADR-084: zero-result reason callout -----------------------------------


def test_zero_reason_callout_empty_when_all_sources_clean() -> None:
    stats = [
        {"source": "universal_ai_search", "display_source": "a.com",
         "match_host": "a.com", "fetched": 3, "passed": 2},
        {"source": "ebay_search", "display_source": "ebay_search",
         "match_host": None, "fetched": 5, "passed": 1},
    ]
    assert _build_zero_reason_callout(stats) == ""


def test_zero_reason_callout_classifies_and_skips_clean() -> None:
    stats = [
        # clean — must not appear
        {"source": "universal_ai_search", "display_source": "good.com",
         "match_host": "good.com", "fetched": 2, "passed": 2},
        # transient: AlterLab pool exhausted
        {"source": "universal_ai_search", "display_source": "amazon.com",
         "match_host": "amazon.com", "fetched": 0, "passed": 0,
         "diagnostics": {"body_len": 0, "alterlab_pool_exhausted": True}},
        # parser gap: full body, 0 parsed. Use a synthetic host so the test
        # isn't coupled to whether a real vendor is currently `known_failure`
        # in the committed registry (e.g. ADR-089 moved bhphotovideo.com from
        # parser-gap to known_failure, which would flip this assertion).
        {"source": "universal_ai_search", "display_source": "mysterystore.example",
         "match_host": "mysterystore.example", "fetched": 0, "passed": 0,
         "diagnostics": {"body_len": 200_000}},
        # no match: fetched but none qualified
        {"source": "universal_ai_search", "display_source": "newegg.com",
         "match_host": "newegg.com", "fetched": 7, "passed": 0},
    ]
    callout = _build_zero_reason_callout(stats)
    assert "good.com" not in callout
    assert "**amazon.com** — _transient_" in callout
    assert "**mysterystore.example** — _needs work_" in callout
    assert "**newegg.com** — _no match_" in callout
    # No permanent source → NOTE, not WARNING.
    assert callout.startswith("> [!NOTE]")


def test_zero_reason_callout_known_failure_is_warning() -> None:
    # microcenter.com carries a known_failure in the committed vendor_quirks.
    stats = [
        {"source": "universal_ai_search", "display_source": "microcenter.com",
         "match_host": "microcenter.com", "fetched": 0, "passed": 0,
         "diagnostics": {"body_len": 0}},
    ]
    callout = _build_zero_reason_callout(stats)
    assert callout.startswith("> [!WARNING]")
    assert "**microcenter.com** — _blocked_" in callout


def test_build_zero_reason_callout_includes_per_source_httperror() -> None:
    """D2 regression (Phase 27 / STRESS_TEST_26.md § Defect 2).

    Live shape from stress26-xm5: four ``bestbuy.com`` source rows sharing the
    same host — one succeeded (``fetched=4, passed=2``) and three errored with
    HTTP/2 stream errors after the AlterLab fallback to curl_cffi. Pre-fix,
    the cli's ``passed_by_key`` was keyed by host only, so all four rows ended
    up rendering ``Passed | 2``; the classifier's ``passed > 0`` short-circuit
    then silently treated the three error rows as OK and produced NO bullet.

    Post-fix the cli stamps a ``source_url`` into each emitted listing's
    ``attrs`` and keys the join by ``(source_id, host, url)``. The cli build
    therefore produces ``passed == 0`` on each error row, which is exactly
    what this test exercises: feed the classifier the shape it would actually
    receive after the fix, and assert each error row lands in the callout
    with the ``transient`` category. (The cli-side per-source attribution is
    independently covered by ``test_passed_match_key_carries_source_url_*``.)
    """
    stats = [
        {"source": "universal_ai_search", "display_source": "bestbuy.com",
         "match_host": "bestbuy.com",
         "match_url": "https://www.bestbuy.com/site/searchpage.jsp?st=sony+wh-1000xm5",
         "fetched": 4, "passed": 2, "error": None},
        {"source": "universal_ai_search", "display_source": "bestbuy.com",
         "match_host": "bestbuy.com",
         "match_url": "https://www.bestbuy.com/site/sony-wh-1000xm5-black/6505794.p?skuId=6505794",
         "fetched": 0, "passed": 0,
         "error": "HTTPError: HTTP/2 stream 1 was not closed cleanly: "
                  "INTERNAL_ERROR (err 2)"},
        {"source": "universal_ai_search", "display_source": "bestbuy.com",
         "match_host": "bestbuy.com",
         "match_url": "https://www.bestbuy.com/site/sony-wh-1000xm5-silver/6505795.p?skuId=6505795",
         "fetched": 0, "passed": 0,
         "error": "HTTPError: HTTP/2 stream 1 was not closed cleanly: "
                  "INTERNAL_ERROR (err 2)"},
        {"source": "universal_ai_search", "display_source": "bestbuy.com",
         "match_host": "bestbuy.com",
         "match_url": "https://www.bestbuy.com/site/sony-wh-1000xm5-smoky-pink/6505796.p?skuId=6505796",
         "fetched": 0, "passed": 0,
         "error": "HTTPError: HTTP/2 stream 1 was not closed cleanly: "
                  "INTERNAL_ERROR (err 2)"},
    ]
    callout = _build_zero_reason_callout(stats)
    # The OK row (passed=2) must NOT appear; the three error rows MUST appear,
    # each as a `transient` bullet (rule 8 in source_reasons.py).
    transient_bullets = [
        line for line in callout.split("\n")
        if "**bestbuy.com** — _transient_" in line
    ]
    assert len(transient_bullets) == 3, (
        f"expected 3 transient bestbuy.com bullets (one per failed URL); "
        f"got {len(transient_bullets)} in callout:\n{callout}"
    )
    # The bullets carry the short error excerpt so the user can debug.
    for line in transient_bullets:
        assert "HTTP/2 stream" in line

