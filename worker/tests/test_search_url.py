"""Tests for ADR-105 registry-driven search-URL rendering.

``render_search_url`` is the single source of truth shared (parity-checked)
with the TypeScript ``renderSearchUrl`` in web/lib/onboard/search-url-shared.ts.
The shared fixture ``fixtures/search_url/cases.json`` pins both halves; the web
side asserts the same cases in ``scripts/check-search-url-parity.test.mjs``.
"""

from __future__ import annotations

import json
from pathlib import Path

from product_search.vendor_quirks import render_search_url

FIXTURE = Path(__file__).parent / "fixtures" / "search_url" / "cases.json"


def test_render_search_url_matches_parity_fixture() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    assert cases, "fixture has no cases"
    for c in cases:
        assert render_search_url(c["host"], c["query"]) == c["expected_url"], (
            f"render_search_url diverged from the parity contract for "
            f"host={c['host']!r} query={c['query']!r}"
        )


def test_microcenter_uses_ntt_not_brand_facet() -> None:
    """The headline ADR-105 regression: the 2026-05-27 DJI run guessed
    `fq=brand:DJI` (a brand facet that returned the whole DJI catalog). The
    registry template must produce the keyword param `Ntt=` instead.
    """
    url = render_search_url("microcenter.com", "DJI Neo 2 Motion Fly More Combo")
    assert url is not None
    assert "Ntt=" in url
    assert "fq=brand" not in url
    assert url.startswith("https://www.microcenter.com/search/search_results.aspx?")


def test_unknown_host_returns_none() -> None:
    assert render_search_url("no-template-vendor.example", "anything") is None
