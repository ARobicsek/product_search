"""Tests for the universal_ai adapter (Phase 12d).

The adapter is split into two halves:

1. ``_extract_candidates`` — pure-Python anchor extraction with selectolax.
   Pinned against ``fixtures/universal_ai/synthetic_vendor.html`` so changes
   to the heuristics surface as test diffs, not silent regressions.

2. ``fetch`` — fetch + extract + LLM pick. Tests stub both ``_fetch_html``
   and ``call_llm`` so no network or API key is needed. The LLM is asked
   to return ``{idx, title, price_usd, condition}`` keyed by the candidate
   index; the test asserts the resulting Listings carry the verbatim
   candidate URL (no LLM hallucination of URLs).

We DON'T test against real vendor HTML in CI — the fixture is intentionally
synthetic so it can't drift when a vendor redesigns their site. A real-site
exercise belongs in a manual smoke run, not the test suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from product_search.adapters import universal_ai
from product_search.llm import LLMResponse
from product_search.models import AdapterQuery

FIXTURE = Path(__file__).parent / "fixtures" / "universal_ai" / "synthetic_vendor.html"
BASE_URL = "https://www.synthvendor.com/collections/headphones"


@pytest.fixture(autouse=True)
def _no_fixture_short_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Other test files set WORKER_USE_FIXTURES=1, which makes universal_ai
    short-circuit to []. Clear it so these tests exercise the real path."""
    monkeypatch.delenv("WORKER_USE_FIXTURES", raising=False)


def _load_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


# --- Candidate extraction ---------------------------------------------------


def test_extract_skips_navigation_and_chrome() -> None:
    cands = universal_ai._extract_candidates(_load_html(), base_url=BASE_URL)
    hrefs = [c["href"] for c in cands]

    assert all("/cart" not in h for h in hrefs), "cart link should be filtered"
    assert all("/account" not in h for h in hrefs), "account link should be filtered"
    assert all("mailto:" not in h for h in hrefs), "mailto links must be skipped"
    assert all("tel:" not in h for h in hrefs), "tel links must be skipped"
    assert all("/search" not in h for h in hrefs), "search-results URLs must be filtered"
    assert all("/collections/" not in h for h in hrefs), "category links must be filtered"


def test_extract_resolves_relative_and_absolute_urls() -> None:
    cands = universal_ai._extract_candidates(_load_html(), base_url=BASE_URL)
    hrefs = {c["href"] for c in cands}

    assert "https://www.synthvendor.com/products/synth-noise-cancelling-700" in hrefs
    assert "https://www.synthvendor.com/products/synth-700-refurb" in hrefs
    assert "https://www.synthvendor.com/products/synth-700-used-fair" in hrefs


def test_extract_dedupes_by_canonical_url() -> None:
    """Two anchors pointing at the same scheme+host+path collapse to one."""
    cands = universal_ai._extract_candidates(_load_html(), base_url=BASE_URL)
    canonical_paths = [c["href"].split("?")[0] for c in cands]
    assert canonical_paths.count(
        "https://www.synthvendor.com/products/synth-noise-cancelling-700"
    ) == 1


def test_extract_attaches_price_hints_from_card() -> None:
    cands = universal_ai._extract_candidates(_load_html(), base_url=BASE_URL)
    by_href = {c["href"]: c for c in cands}

    nc700 = by_href["https://www.synthvendor.com/products/synth-noise-cancelling-700"]
    assert any("249.99" in p for p in nc700["price_hints"])

    refurb = by_href["https://www.synthvendor.com/products/synth-700-refurb"]
    assert any("179.50" in p for p in refurb["price_hints"])

    used = by_href["https://www.synthvendor.com/products/synth-700-used-fair"]
    assert any("129.00" in p for p in used["price_hints"])


def test_extract_keeps_priceless_anchor_when_url_looks_product_like() -> None:
    """The 'coming soon' card has no price but a /products/ URL — keep it
    so the LLM can decide whether to omit (current contract: omit for no price)."""
    cands = universal_ai._extract_candidates(_load_html(), base_url=BASE_URL)
    hrefs = {c["href"] for c in cands}
    assert "https://www.synthvendor.com/products/coming-soon-flagship" in hrefs


# --- Full fetch() with mocked HTTP + mocked LLM -----------------------------


def _stub_llm_response(text: str) -> Any:
    def _call(**_: object) -> LLMResponse:
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text=text, input_tokens=200, output_tokens=80,
        )
    return _call


def test_fetch_emits_listings_with_verbatim_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: stubbed fetch + stubbed LLM produces Listings whose
    URLs are exact candidate URLs (no LLM URL hallucination possible)."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0: (_load_html(), 200, "stub"),
    )

    def _llm(**kwargs: object) -> LLMResponse:
        # The LLM keeps three real listings, omits the priceless one.
        # idx values must match the order anchors appear in the candidates list.
        # We don't pre-assume the order — instead, look up by the payload.
        import json as _json
        payload = _json.loads(kwargs["messages"][0].content)  # type: ignore[index]
        # Map anchor_text → idx so we can target by content, not order.
        idx_by_text = {c["anchor_text"]: c["idx"] for c in payload}
        decisions = []
        for text, price, condition in [
            ("SynthBose Noise Cancelling Headphones 700", 249.99, "new"),
            ("SynthBose Headphones 700 — Refurbished", 179.50, "refurbished"),
            ("Used SynthBose 700 — fair condition", 129.00, "used"),
        ]:
            if text in idx_by_text:
                decisions.append({
                    "idx": idx_by_text[text],
                    "title": text,
                    "price_usd": price,
                    "condition": condition,
                })
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text=_json.dumps({"listings": decisions}),
            input_tokens=300, output_tokens=120,
        )

    monkeypatch.setattr(universal_ai, "call_llm", _llm)

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    results = universal_ai.fetch(query)

    assert len(results) == 3
    urls = {r.url for r in results}
    assert "https://www.synthvendor.com/products/synth-noise-cancelling-700" in urls
    assert "https://www.synthvendor.com/products/synth-700-refurb" in urls
    assert "https://www.synthvendor.com/products/synth-700-used-fair" in urls

    by_url = {r.url: r for r in results}
    nc700_url = "https://www.synthvendor.com/products/synth-noise-cancelling-700"
    refurb_url = "https://www.synthvendor.com/products/synth-700-refurb"
    used_url = "https://www.synthvendor.com/products/synth-700-used-fair"
    assert by_url[nc700_url].unit_price_usd == 249.99
    assert by_url[refurb_url].condition == "refurbished"
    assert by_url[used_url].condition == "used"

    assert universal_ai.LAST_RUN_USAGE is not None
    assert universal_ai.LAST_RUN_USAGE["step"] == "universal_ai_search"
    assert universal_ai.LAST_RUN_USAGE["model"] == "claude-haiku-4-5"


def test_fetch_drops_invented_indices(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM returning idx values outside the candidate range must not crash
    or emit Listings with bogus URLs."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0: (_load_html(), 200, "stub"),
    )
    monkeypatch.setattr(universal_ai, "call_llm", _stub_llm_response(
        '{"listings": ['
        '{"idx": 999, "title": "Phantom", "price_usd": 1.0, "condition": "new"}'
        ']}'
    ))

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    assert universal_ai.fetch(query) == []


def test_fetch_tolerates_prose_preamble(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared _extract_json walks past a prose preamble (mirrors ai_filter)."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0: (_load_html(), 200, "stub"),
    )
    monkeypatch.setattr(universal_ai, "call_llm", _stub_llm_response(
        'Here are the listings I found:\n\n'
        '{"listings": []}'
    ))

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    assert universal_ai.fetch(query) == []


def test_fetch_returns_empty_when_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    query = AdapterQuery(source_id="universal_ai_search", extra={})
    assert universal_ai.fetch(query) == []


def test_fetch_returns_empty_when_html_has_no_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bot-blocked pages often serve an empty challenge body — that path
    must short-circuit cleanly without burning an LLM call."""
    challenge_html = "<html><body><p>Just a Cloudflare challenge.</p></body></html>"
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0: (challenge_html, 200, "stub"),
    )

    def _should_not_be_called(**_: object) -> LLMResponse:  # pragma: no cover
        raise AssertionError("LLM must not be called when no candidates found")

    monkeypatch.setattr(universal_ai, "call_llm", _should_not_be_called)

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    assert universal_ai.fetch(query) == []
