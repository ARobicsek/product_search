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

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "universal_ai"
FIXTURE = FIXTURE_DIR / "synthetic_vendor.html"
SHOPIFY_JSONLD_FIXTURE = FIXTURE_DIR / "shopify_jsonld.html"
CUSTOM_AGGREGATE_FIXTURE = FIXTURE_DIR / "custom_aggregate_offer.html"
# Phase 15: real-vendor fixtures captured 2026-05-03 from live AlterLab /
# httpx fetches. Pinned against extractor changes to surface heuristic
# regressions; intentionally large so they exercise the per-canonical-URL
# merge path on real-world DOM density.
HEADPHONES_COM_FIXTURE = FIXTURE_DIR / "headphones-com-shopify-collection.html"
TARGET_FIXTURE = FIXTURE_DIR / "target-search-bose.html"
BHPHOTO_FIXTURE = FIXTURE_DIR / "bhphotovideo-search-bose.html"
# Phase 28 (ADR-087): the two evidenced search-page recall leaks.
#   - Newegg: captured 2026-05-25 via `cli probe-url --render --wait-condition
#     networkidle`. 529 KB, status 200, 92 titled anchors, 0 JSON-LD, ~20 real
#     "Logitech MX Master 3S" product tiles with prices in the rendered text.
#     The Phase 26 Defect 6 zero was a transient render miss (degraded AlterLab
#     served an un-hydrated body), NOT a parser gap — this fixture proves the
#     extractor recovers the products when the page actually renders.
#   - B&H: captured 2026-05-25. Every render rung (tier 3/4, networkidle and
#     domcontentloaded) returned the SAME 31.7 KB Cloudflare "Performing
#     security verification" challenge — search is bot-walled, same class as
#     microcenter. Recall for B&H comes from detail URLs (prefer_page_type:
#     detail in the registry), never search.
NEWEGG_SEARCH_FIXTURE = FIXTURE_DIR / "newegg_search_mx_master_3s.html"
BHPHOTO_SEARCH_FIXTURE = FIXTURE_DIR / "bhphotovideo_search_mx_master_3s.html"
CENTRALCOMPUTER_CHALLENGE_FIXTURE = (
    FIXTURE_DIR / "centralcomputer_search_cloudflare_challenge_2026_05_25.html"
)
SERVERSUPPLY_CHALLENGE_FIXTURE = (
    FIXTURE_DIR / "serversupply_cloudflare_challenge_2026_05_25.html"
)
BASE_URL = "https://www.synthvendor.com/collections/headphones"
SHOPIFY_BASE_URL = "https://shop.synthstore.example.com/collections/headphones"
CUSTOM_BASE_URL = "https://customvendor.example.com/collections/all"
HEADPHONES_COM_URL = "https://www.headphones.com/collections/noise-cancelling-headphones"
TARGET_URL = "https://www.target.com/s?searchTerm=bose+nc+700+headphones"
BHPHOTO_URL = "https://www.bhphotovideo.com/c/search?Ntt=bose+nc+700+headphones"


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
        lambda url, timeout=20.0, **_kw: (_load_html(), 200, "stub"),
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
        lambda url, timeout=20.0, **_kw: (_load_html(), 200, "stub"),
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
        lambda url, timeout=20.0, **_kw: (_load_html(), 200, "stub"),
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


# --- ADR-068: vendor_quirks registry integration --------------------------


def _capture_fetch_args(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace _fetch_html_with_retry with a stub that records its args
    and returns an empty body so the rest of the pipeline short-circuits."""
    captured: dict[str, Any] = {}

    def _stub(url: str, alterlab_options: dict[str, Any] | None = None) -> tuple[str, int, str]:
        captured["url"] = url
        captured["alterlab_options"] = alterlab_options
        return ("", 200, "stub")

    # These tests assert the MERGED options that reach the fetch layer. The
    # ADR-071 escalation path (which re-fetches with stronger options on a weak
    # render) only runs when ALTERLAB_API_KEY is set; the real .env can leak it
    # into the process via another test's imports, so delete it here to keep the
    # single-fetch capture deterministic regardless of test ordering.
    monkeypatch.delenv("ALTERLAB_API_KEY", raising=False)
    monkeypatch.setattr(universal_ai, "_fetch_html_with_retry", _stub)
    return captured


def test_vendor_quirks_applies_bestbuy_nosplash_transform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter must rewrite Best Buy search URLs to include
    intl=nosplash — the regression guard for the e93fd47 fix."""
    captured = _capture_fetch_args(monkeypatch)
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": "https://www.bestbuy.com/site/searchpage.jsp?st=wh-1000xm5"},
    )
    universal_ai.fetch(query)
    assert "intl=nosplash" in captured["url"], captured["url"]


def test_vendor_quirks_merges_default_alterlab_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Best Buy URL with no source-level alterlab_options must pick up
    {country:us, min_tier:3} from the registry defaults."""
    captured = _capture_fetch_args(monkeypatch)
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": "https://www.bestbuy.com/site/searchpage.jsp?st=foo"},
    )
    universal_ai.fetch(query)
    assert captured["alterlab_options"] == {"country": "us", "min_tier": 3}


def test_vendor_quirks_source_alterlab_options_override_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source-level options win on key conflict; defaults fill in the rest."""
    captured = _capture_fetch_args(monkeypatch)
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={
            "url": "https://www.bestbuy.com/site/searchpage.jsp?st=foo",
            "alterlab_options": {"min_tier": 4, "wait_condition": "load"},
        },
    )
    universal_ai.fetch(query)
    opts = captured["alterlab_options"]
    assert opts["min_tier"] == 4   # source-level override
    assert opts["wait_condition"] == "load"  # source-only
    assert opts["country"] == "us"  # filled from defaults


def test_vendor_quirks_skip_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra.skip_vendor_quirks: true must bypass BOTH URL transforms AND
    default option merging — escape hatch for profiles that intentionally
    want the raw URL/options."""
    captured = _capture_fetch_args(monkeypatch)
    raw_url = "https://www.bestbuy.com/site/searchpage.jsp?st=foo"
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": raw_url, "skip_vendor_quirks": True},
    )
    universal_ai.fetch(query)
    assert captured["url"] == raw_url  # not transformed
    assert captured["alterlab_options"] is None  # defaults not merged


def test_vendor_quirks_unknown_host_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unregistered host must pass through untouched."""
    captured = _capture_fetch_args(monkeypatch)
    raw_url = "https://unknown-vendor.example/search?q=x"
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": raw_url},
    )
    universal_ai.fetch(query)
    assert captured["url"] == raw_url
    assert captured["alterlab_options"] is None


# --- ADR-053: bounded retry on transient fetch failures --------------------


def test_is_retryable_fetch_error_classifies_correctly() -> None:
    """curl(28)/timeout/connection class is retryable; AlterLab auth and
    plain parse errors are not."""
    provantage = Exception(
        "Timeout: Failed to perform, curl: (28) Connection timed out "
        "after 20002 milliseconds."
    )
    assert universal_ai._is_retryable_fetch_error(provantage) is True
    assert universal_ai._is_retryable_fetch_error(TimeoutError("slow")) is True
    assert universal_ai._is_retryable_fetch_error(
        ConnectionError("connection reset by peer")
    ) is True

    # AlterLab quota/auth must NOT retry (a 2nd 120 s AlterLab call can't fix it).
    alterlab_429 = RuntimeError("AlterLab API issue: HTTP 429 quota or auth error")
    assert universal_ai._is_retryable_fetch_error(alterlab_429) is False
    # A non-transient bug must surface immediately, not after a pointless retry.
    assert universal_ai._is_retryable_fetch_error(ValueError("bad json")) is False


def test_fetch_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single transient timeout is retried once and the source recovers
    (the provantage 2026-05-18 failure mode)."""
    monkeypatch.setattr(universal_ai, "_FETCH_RETRY_BACKOFF_SECONDS", 0.0)
    slept: list[float] = []
    monkeypatch.setattr(universal_ai.time, "sleep", lambda s: slept.append(s))

    calls: list[str] = []

    def _flaky(url: str, timeout: float = 20.0) -> tuple[str, int, str]:
        calls.append(url)
        if len(calls) == 1:
            raise Exception(
                "Timeout: Failed to perform, curl: (28) Connection timed out "
                "after 20002 milliseconds."
            )
        return (_load_html(), 200, "stub")

    monkeypatch.setattr(universal_ai, "_fetch_html", _flaky)
    monkeypatch.setattr(universal_ai, "call_llm", _stub_llm_response('{"listings": []}'))

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    assert universal_ai.fetch(query) == []
    assert len(calls) == 2  # failed once, retried once, succeeded
    assert len(slept) == 1


def test_fetch_does_not_retry_alterlab_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth/quota errors bubble up on the first attempt — no wasted retry."""
    monkeypatch.setattr(universal_ai, "_FETCH_RETRY_BACKOFF_SECONDS", 0.0)
    calls: list[str] = []

    def _auth_fail(url: str, timeout: float = 20.0) -> tuple[str, int, str]:
        calls.append(url)
        raise RuntimeError("AlterLab API issue: HTTP 429 quota or auth error")

    monkeypatch.setattr(universal_ai, "_fetch_html", _auth_fail)

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    with pytest.raises(RuntimeError, match="AlterLab API issue"):
        universal_ai.fetch(query)
    assert len(calls) == 1  # NOT retried


def test_fetch_does_not_retry_non_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-timeout error surfaces immediately rather than after a retry."""
    monkeypatch.setattr(universal_ai, "_FETCH_RETRY_BACKOFF_SECONDS", 0.0)
    calls: list[str] = []

    def _boom(url: str, timeout: float = 20.0) -> tuple[str, int, str]:
        calls.append(url)
        raise ValueError("unexpected parse failure")

    monkeypatch.setattr(universal_ai, "_fetch_html", _boom)

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    with pytest.raises(ValueError, match="unexpected parse failure"):
        universal_ai.fetch(query)
    assert len(calls) == 1


def test_alterlab_fetch_path_used_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ALTERLAB_API_KEY is set, _fetch_html routes through AlterLab
    (not curl_cffi/httpx) and returns the origin HTML from the JSON envelope."""
    monkeypatch.setenv("ALTERLAB_API_KEY", "test-key-12345")

    captured: dict[str, Any] = {}

    class _StubClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            json: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> Any:
            captured["api_url"] = url
            captured["json"] = json
            captured["headers"] = headers

            class _Resp:
                status_code = 200

                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict[str, Any]:
                    return {
                        "status_code": 200,
                        "content": {"html": "<html><body>alterlab!</body></html>"},
                    }

            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _StubClient)

    html, status, fetcher = universal_ai._fetch_html("https://example.com/products")
    assert fetcher == "alterlab"
    assert status == 200
    assert "alterlab!" in html
    assert captured["api_url"] == "https://api.alterlab.io/api/v1/scrape"
    assert captured["headers"]["X-API-Key"] == "test-key-12345"
    assert captured["json"]["url"] == "https://example.com/products"
    assert captured["json"]["sync"] is True
    assert captured["json"]["advanced"]["render_js"] is True


def test_alterlab_failure_falls_back_to_lower_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """AlterLab outage / 5xx must not zero a run; we fall back to
    curl_cffi/httpx so existing free-fetch sites keep working."""
    monkeypatch.setenv("ALTERLAB_API_KEY", "test-key")

    def _alterlab_explodes(url: str, key: str, *, timeout: float = 60.0) -> Any:
        raise RuntimeError("alterlab is down")

    fallback_called: dict[str, bool] = {"hit": False}

    def _fake_curl_get(*_args: object, **_kwargs: object) -> Any:
        fallback_called["hit"] = True

        class _Resp:
            text = "<html><body>fallback ok</body></html>"
            status_code = 200

        return _Resp()

    monkeypatch.setattr(universal_ai, "_fetch_via_alterlab", _alterlab_explodes)

    # Fake the curl_cffi import inside _fetch_html.
    import sys
    import types as _types
    fake_cc = _types.ModuleType("curl_cffi")
    fake_requests = _types.ModuleType("curl_cffi.requests")
    fake_requests.get = _fake_curl_get  # type: ignore[attr-defined]
    fake_cc.requests = fake_requests  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_cc)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_requests)

    html, status, fetcher = universal_ai._fetch_html("https://example.com/products")
    assert fallback_called["hit"]
    assert fetcher == "curl_cffi"
    assert status == 200
    assert "fallback ok" in html


def test_no_alterlab_key_skips_alterlab_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ALTERLAB_API_KEY, _fetch_via_alterlab must NOT be called —
    we go straight to curl_cffi/httpx so free-only setups don't break."""
    monkeypatch.delenv("ALTERLAB_API_KEY", raising=False)

    def _should_not_be_called(*_a: object, **_k: object) -> Any:  # pragma: no cover
        raise AssertionError("AlterLab path must not run when key is unset")

    monkeypatch.setattr(universal_ai, "_fetch_via_alterlab", _should_not_be_called)

    # Stub curl_cffi so the real fetch doesn't try to hit the network.
    import sys
    import types as _types

    def _fake_get(*_a: object, **_k: object) -> Any:
        class _Resp:
            text = "<html></html>"
            status_code = 200
        return _Resp()

    fake_cc = _types.ModuleType("curl_cffi")
    fake_requests = _types.ModuleType("curl_cffi.requests")
    fake_requests.get = _fake_get  # type: ignore[attr-defined]
    fake_cc.requests = fake_requests  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_cc)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", fake_requests)

    _, _, fetcher = universal_ai._fetch_html("https://example.com")
    assert fetcher == "curl_cffi"


# --- Phase 15: JSON-LD tier -------------------------------------------------


def test_jsonld_extracts_shopify_itemlist() -> None:
    """Shopify ItemList → ListItem → Product is the dominant collection-page
    pattern. Extracts name + price + url, drops the priceless pre-order card,
    skips the Organization block."""
    html = SHOPIFY_JSONLD_FIXTURE.read_text(encoding="utf-8")
    listings = universal_ai._extract_jsonld_listings(html, base_url=SHOPIFY_BASE_URL)

    by_url = {item["url"]: item for item in listings}
    assert len(listings) == 2, f"expected 2, got {len(listings)}: {listings}"

    qc700 = by_url["https://shop.synthstore.example.com/products/qc700"]
    assert qc700["title"] == "Synthbose QuietComfort 700"
    assert qc700["price_usd"] == 299.99
    assert qc700["condition"] == "new"

    refurb = by_url["https://shop.synthstore.example.com/products/qc700-refurb"]
    assert refurb["price_usd"] == 219.50
    assert refurb["condition"] == "refurbished"

    # Pre-order Product had no offers → must be dropped (no invented price).
    assert "qc900-preorder" not in " ".join(by_url.keys())


def test_jsonld_handles_aggregate_offer_and_offer_list() -> None:
    """AggregateOffer → use lowPrice. Offer list → take cheapest. @type
    can be a list. Malformed JSON-LD blocks must be skipped, not crash."""
    html = CUSTOM_AGGREGATE_FIXTURE.read_text(encoding="utf-8")
    listings = universal_ai._extract_jsonld_listings(html, base_url=CUSTOM_BASE_URL)

    by_url = {item["url"]: item for item in listings}
    assert len(listings) == 2

    pro = by_url["https://customvendor.example.com/listing/123"]
    assert pro["price_usd"] == 189.00  # lowPrice from AggregateOffer
    assert pro["condition"] == "used"

    lite = by_url["https://customvendor.example.com/listing/456"]
    assert lite["price_usd"] == 49.99  # cheapest of the offer list


def test_jsonld_returns_empty_for_synthetic_no_jsonld() -> None:
    """The original synthetic_vendor.html fixture has no JSON-LD blocks;
    extractor must return [] cleanly so the anchor tier still runs."""
    html = FIXTURE.read_text(encoding="utf-8")
    assert universal_ai._extract_jsonld_listings(html, base_url=BASE_URL) == []


def test_jsonld_returns_empty_for_organization_only() -> None:
    """A page with only an Organization JSON-LD block (e.g. gazelle's 404)
    yields no Product listings — must not falsely emit anything."""
    html = """
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Organization",
     "name": "TestStore", "url": "https://test.example.com"}
    </script>
    """
    assert universal_ai._extract_jsonld_listings(html, base_url="https://test.example.com") == []


def test_fetch_preserves_jsonld_in_search_union(monkeypatch: pytest.MonkeyPatch) -> None:
    """When JSON-LD yields listings on a search URL, the union still runs
    the anchor-walker and full-HTML tiers (for additive recall), but the
    JSON-LD results must be preserved in the merged output."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (
            SHOPIFY_JSONLD_FIXTURE.read_text(encoding="utf-8"), 200, "stub",
        ),
    )

    # The LLM tiers will be called — return empty results so JSON-LD is the
    # sole contributor (realistic: the LLM won't find products already in
    # JSON-LD, and the Shopify fixture has no extra unlisted products).
    monkeypatch.setattr(universal_ai, "call_llm", _stub_llm_response('{"listings": []}'))

    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": SHOPIFY_BASE_URL},
    )
    results = universal_ai.fetch(query)

    assert len(results) == 2
    urls = {r.url for r in results}
    assert "https://shop.synthstore.example.com/products/qc700" in urls
    assert "https://shop.synthstore.example.com/products/qc700-refurb" in urls
    # JSON-LD results are tagged with the jsonld extractor.
    assert all(r.attrs.get("extractor") == "jsonld" for r in results)


def test_fetch_returns_empty_when_html_has_no_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bot-blocked pages often serve an empty challenge body — that path
    must short-circuit cleanly without burning an LLM call."""
    challenge_html = "<html><body><p>Just a Cloudflare challenge.</p></body></html>"
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (challenge_html, 200, "stub"),
    )

    def _should_not_be_called(**_: object) -> LLMResponse:  # pragma: no cover
        raise AssertionError("LLM must not be called when no candidates found")

    monkeypatch.setattr(universal_ai, "call_llm", _should_not_be_called)

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    assert universal_ai.fetch(query) == []


# --- Phase 15: heuristic helpers ------------------------------------------


def test_looks_like_nav_path_blocks_cms_paths() -> None:
    """The new path-prefix filter catches Shopify /pages/ + /blogs/, big-box
    /store-locator + /weekly-ad, etc. — all places real product URLs never live."""
    nav_examples = [
        "https://shop.example.com/pages/contact-us",
        "https://shop.example.com/blogs/buying-guides",
        "https://shop.example.com/articles/how-to",
        "https://shop.example.com/help/shipping",
        "https://shop.example.com/policies/refund",
        "https://www.target.com/store-locator/find-stores",
        "https://example.com/",  # bare home page
        "https://example.com",
        "https://example.com/account",
        "https://example.com/account/orders",
    ]
    # Note: paths like ``/weekly-ad`` aren't path-disqualified — the anchor
    # text "Weekly Ad" is caught by the ``_UI_CHROME_TEXTS`` backstop instead.
    product_examples = [
        # Hyphenated path tail that LOOKS nav-ish but is a product slug.
        "https://shop.example.com/products/about-our-bose-headphones",
        "https://shop.example.com/products/contact-edition-headphones",
        # /p/<slug> is the Target product shape.
        "https://www.target.com/p/bose-quietcomfort-headphones/-/A-95032709",
    ]
    for url in nav_examples:
        assert universal_ai._looks_like_nav_path(url), f"should disqualify nav: {url}"
    for url in product_examples:
        assert not universal_ai._looks_like_nav_path(url), (
            f"should NOT disqualify product: {url}"
        )


def test_anchor_title_falls_back_to_image_alt() -> None:
    """Big-box product cards often wrap just an <img alt="..."> — when the
    anchor's own text is empty we must lift the alt or aria-label / title."""
    from selectolax.parser import HTMLParser

    html = """
    <html><body>
      <a href="/p/x"><img alt="Bose QuietComfort Headphones" src="/x.jpg"></a>
      <a href="/p/y" aria-label="Sennheiser Momentum 4"><span></span></a>
      <a href="/p/z" title="Apple AirPods Pro"></a>
      <a href="/p/skip-empty"><span></span></a>
    </body></html>
    """
    tree = HTMLParser(html)
    anchors = tree.css("a")
    assert universal_ai._anchor_title(anchors[0]) == "Bose QuietComfort Headphones"
    assert universal_ai._anchor_title(anchors[1]) == "Sennheiser Momentum 4"
    assert universal_ai._anchor_title(anchors[2]) == "Apple AirPods Pro"
    assert universal_ai._anchor_title(anchors[3]) == ""


# --- Phase 15: real-vendor fixtures ---------------------------------------
#
# These fixtures are big (300KB-900KB each) but pin the extractor against
# real-world DOM density. Each test asserts a quality bar — # of candidates,
# % with non-empty title, % with price hints — rather than exact contents,
# so it survives small site redesigns until something fundamental breaks.


def test_extract_headphones_com_shopify_collection() -> None:
    """Shopify collection page with the split-card markup that broke the
    pre-Phase-15 extractor: title-anchor in card__inner, price-anchor in a
    sibling card__content. The two-pass merge collapses both onto a single
    candidate carrying both title AND price hints.
    """
    html = HEADPHONES_COM_FIXTURE.read_text(encoding="utf-8")
    cands = universal_ai._extract_candidates(html, base_url=HEADPHONES_COM_URL)

    assert len(cands) >= 20, f"expected ≥20 candidates, got {len(cands)}"
    # Most candidates must have a real title — empty-title rate >= 80% would
    # mean the image-alt fallback regressed.
    with_title = sum(1 for c in cands if c["anchor_text"])
    assert with_title >= int(0.9 * len(cands)), (
        f"title coverage too low: {with_title}/{len(cands)}"
    )
    # Most candidates must have a price hint — Phase 15 fixed the bug where
    # all candidates had 0 price hints because of dedupe-keeps-wrong-anchor.
    with_price = sum(1 for c in cands if c["price_hints"])
    assert with_price >= int(0.7 * len(cands)), (
        f"price coverage too low: {with_price}/{len(cands)}"
    )

    # Spot-check: a known product should be in there with a plausible price.
    titles = [c["anchor_text"] for c in cands]
    assert any("Focal Clear" in t for t in titles), (
        "expected the Focal Clear Headphones card"
    )

    # CMS-nav paths must NOT appear (the regression filter is doing its job).
    hrefs = [c["href"] for c in cands]
    assert all("/pages/" not in h for h in hrefs)
    assert all("/blogs/" not in h for h in hrefs)


def test_extract_target_search_image_only_anchors() -> None:
    """Target search results are React-hydrated cards where the product
    anchor wraps just an <img alt="..."> — pre-Phase-15 these all came back
    with empty anchor_text. The img-alt fallback recovers them.
    """
    html = TARGET_FIXTURE.read_text(encoding="utf-8")
    cands = universal_ai._extract_candidates(html, base_url=TARGET_URL)

    assert len(cands) >= 30, f"expected ≥30 candidates, got {len(cands)}"
    with_title = sum(1 for c in cands if c["anchor_text"])
    assert with_title >= int(0.9 * len(cands)), (
        f"img-alt fallback regressed: {with_title}/{len(cands)} have titles"
    )

    # Target uses /p/<slug>/-/A-<id> — verify the product-URL filter accepts these.
    product_paths = [c for c in cands if "/p/" in c["href"]]
    assert len(product_paths) >= 20, (
        f"expected many /p/ product anchors, got {len(product_paths)}"
    )

    # A spot-check: at least one Bose product should surface.
    titles = " ".join(c["anchor_text"] for c in cands).lower()
    assert "bose" in titles


def test_extract_bhphoto_blocked_react_shell_yields_few_candidates() -> None:
    """B&H Photo Video's /c/search page rendered through AlterLab still comes
    back as a React shell with no product anchors in the DOM — only nav and
    promo links. Pin this so the onboarder probe-url integration can detect
    "this URL won't yield listings" and route to sources_pending. A future
    fix that magically extracts BH listings would surface as a test diff.
    """
    html = BHPHOTO_FIXTURE.read_text(encoding="utf-8")
    cands = universal_ai._extract_candidates(html, base_url=BHPHOTO_URL)

    # Whatever leaks through must NOT have prices — we don't want the LLM
    # to invent a listing on top of pure-nav anchors.
    with_price = sum(1 for c in cands if c["price_hints"])
    assert with_price == 0, (
        f"BH challenge page must not yield priced candidates; got {with_price}"
    )
    # And there should be at most a handful of candidates, all of them
    # nav/promo. If we ever see 10+, BH started rendering products and the
    # test should diff so a human can tighten things up.
    assert len(cands) <= 10, (
        f"BH page is supposed to be a barren shell; got {len(cands)} candidates"
    )


# --- Phase 28 (ADR-087): search-page recall leaks (Newegg + B&H) -----------

NEWEGG_SEARCH_URL = "https://www.newegg.com/p/pl?d=logitech+mx+master+3s"
BHPHOTO_SEARCH_URL = "https://www.bhphotovideo.com/c/search?Ntt=logitech+mx+master+3s"


def test_newegg_search_recall_substrate_present() -> None:
    """Newegg search recall is RECOVERABLE — the deterministic substrate the
    LLM tiers consume is present in a properly-rendered body.

    Phase 26 Defect 6 reported Newegg search → 0 listings off an 820 KB body
    and labelled it a PARSER_GAP. The Phase 28 diagnosis (this fixture, freshly
    rendered with wait_condition=networkidle) refutes that: the page carries
    ~20 real "Logitech MX Master 3S" product tiles with full titles, real
    /p/ product URLs, and prices in the visible text. The earlier zero was a
    transient render miss (degraded AlterLab returning an un-hydrated body),
    not a structural gap.

    This pins the recall PRECONDITION (anchors + verbatim prices) without an
    LLM call, so a future render/strip regression that re-introduces the
    Defect 6 zero fails here loudly.
    """
    html = NEWEGG_SEARCH_FIXTURE.read_text(encoding="utf-8")

    anchors = universal_ai._collect_search_anchors(html, base_url=NEWEGG_SEARCH_URL)
    mx_anchors = [
        a for a in anchors if "mx master 3s" in a["title"].lower()
    ]
    assert len(mx_anchors) >= 10, (
        f"expected ≥10 MX Master 3S product anchors, got {len(mx_anchors)}"
    )
    # Each must resolve to a real Newegg product URL (the LLM picks by index,
    # so the URL is never fabricated — but the substrate must carry them).
    assert all("newegg.com" in a["href_abs"] for a in mx_anchors)

    # The anchor-walker tier (pre-ADR-077 path) also recovers the product:
    # priced MX Master 3S candidates are present, so recall doesn't depend on
    # any single extractor tier.
    cands = universal_ai._extract_candidates(html, base_url=NEWEGG_SEARCH_URL)
    priced_mx = [
        c for c in cands
        if "mx master 3s" in c["anchor_text"].lower() and c["price_hints"]
    ]
    assert len(priced_mx) >= 8, (
        f"expected ≥8 priced MX Master 3S anchor candidates, got {len(priced_mx)}"
    )

    # Anti-fabrication substrate: the full-HTML tier verifies every LLM price
    # verbatim against this stripped text — so the prices the model will quote
    # must actually be here. Spot-check a couple of the candidate price hints.
    text = universal_ai._strip_to_main_text(html, max_chars=None)
    verified = 0
    for c in priced_mx:
        for hint in c["price_hints"]:
            try:
                price = float(hint.lstrip("$").replace(",", ""))
            except ValueError:
                continue
            if universal_ai._price_in_text(price, text):
                verified += 1
                break
    assert verified >= 5, (
        f"expected ≥5 MX Master 3S candidates with a verbatim price in the "
        f"stripped text, got {verified}"
    )


def test_newegg_search_offline_extracts_listings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end with stubbed LLM: real Newegg search HTML → ≥5 priced
    Listings out, with the target MX Master 3S present.

    Mirrors the Target offline test (and the ADR-082 Amazon recall pattern):
    pins that the search-union wiring turns a rendered Newegg search body into
    listings. The stub mirrors the anchor-walker tier (pick the priced
    candidates) and returns no extra products from the full-HTML tier, so the
    assertion measures the deterministic recall floor, not the LLM's mood.
    """
    import json as _json

    html = NEWEGG_SEARCH_FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (html, 200, "stub"),
    )

    def _llm(**kwargs: Any) -> LLMResponse:
        content = kwargs["messages"][0].content
        # Full-HTML tier sends "PAGE TEXT:\n...\n\nLINKS:\n..."; the anchor
        # tier sends a JSON array of candidates. Only stub the anchor tier;
        # the union proves recall without leaning on the full-HTML tier.
        try:
            payload = _json.loads(content)
        except (ValueError, TypeError):
            return LLMResponse(
                provider="anthropic", model="claude-haiku-4-5",
                text=_json.dumps({"products": []}),
                input_tokens=100, output_tokens=10,
            )
        decisions = []
        for c in payload:
            if not c.get("price_hints"):
                continue
            price_str = c["price_hints"][0].lstrip("$").replace(",", "")
            try:
                price = float(price_str)
            except ValueError:
                continue
            decisions.append({
                "idx": c["idx"],
                "title": c["anchor_text"] or "Untitled",
                "price_usd": price,
                "condition": "new",
            })
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text=_json.dumps({"listings": decisions}),
            input_tokens=500, output_tokens=200,
        )

    monkeypatch.setattr(universal_ai, "call_llm", _llm)

    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": NEWEGG_SEARCH_URL, "page_type": "search"},
    )
    results = universal_ai.fetch(query)

    assert len(results) >= 5, f"expected ≥5 Listings, got {len(results)}"
    for r in results:
        assert "newegg.com" in r.url  # verbatim candidate URL, never LLM-typed
        assert r.unit_price_usd > 0
    titles = " ".join(r.title.lower() for r in results)
    assert "mx master 3s" in titles, "target product missing from recall set"


def test_bhphoto_search_is_cloudflare_walled_no_priced_candidates() -> None:
    """B&H search is NOT recoverable today — it sits behind a Cloudflare bot
    challenge that even a tier-4 browser render at networkidle can't pass.

    Captured 2026-05-25: tier 3/4 × networkidle/domcontentloaded all returned
    the SAME 31.7 KB "Performing security verification" Cloudflare interstitial
    (Ray ID, cf-* markers) — never the product grid. Same failure class as
    microcenter. The registry routes B&H recall to detail URLs
    (prefer_page_type: detail); this pins that the search body yields ZERO
    priced candidates so the LLM tiers can never fabricate a listing on top of
    a challenge page (ADR-001). A future capture that DOES render products
    would diff here and signal that search recall became recoverable.
    """
    html = BHPHOTO_SEARCH_FIXTURE.read_text(encoding="utf-8")
    assert "security verification" in html.lower(), (
        "fixture should be the Cloudflare challenge page"
    )

    cands = universal_ai._extract_candidates(html, base_url=BHPHOTO_SEARCH_URL)
    with_price = sum(1 for c in cands if c["price_hints"])
    assert with_price == 0, (
        f"challenge page must not yield priced candidates; got {with_price}"
    )
    # The full-HTML tier's anchor substrate is empty on a challenge page too,
    # so it has nothing to enumerate.
    anchors = universal_ai._collect_search_anchors(html, base_url=BHPHOTO_SEARCH_URL)
    titled = [a for a in anchors if a["title"]]
    assert len(titled) <= 5, (
        f"challenge page should expose ~no product anchors; got {len(titled)}"
    )


@pytest.mark.parametrize("fixture,base_url", [
    (
        CENTRALCOMPUTER_CHALLENGE_FIXTURE,
        "https://www.centralcomputer.com/catalogsearch/result/?q=epyc+9255",
    ),
    (
        SERVERSUPPLY_CHALLENGE_FIXTURE,
        "https://www.serversupply.com/",
    ),
])
def test_cloudflare_walled_host_search_yields_no_priced_candidates(
    fixture: Path, base_url: str
) -> None:
    """ADR-088: CentralComputer + ServerSupply are Cloudflare bot-walled. Every
    render rung (tier 3/4 × networkidle/domcontentloaded) returns the SAME
    ~31.8 KB "Just a moment..." Cloudflare interstitial, never products — same
    class as microcenter + B&H search. These fixtures (probed 2026-05-25) pin
    that the challenge body yields ZERO priced candidates so the LLM tiers can
    never fabricate a listing on top of a challenge page (ADR-001), and that
    the registry's `known_failure` routing (→ sources_pending) is the correct
    state. A future capture that DOES render products would diff here and
    signal the Cloudflare wall lifted.
    """
    html = fixture.read_text(encoding="utf-8")
    assert "just a moment" in html.lower(), (
        "fixture should be the Cloudflare challenge page"
    )

    cands = universal_ai._extract_candidates(html, base_url=base_url)
    with_price = sum(1 for c in cands if c["price_hints"])
    assert with_price == 0, (
        f"challenge page must not yield priced candidates; got {with_price}"
    )
    anchors = universal_ai._collect_search_anchors(html, base_url=base_url)
    titled = [a for a in anchors if a["title"]]
    assert len(titled) <= 5, (
        f"challenge page should expose ~no product anchors; got {len(titled)}"
    )


def test_canonicalize_prices_joins_split_amazon_markup() -> None:
    """``$ 329 99`` (selectolax flattening of Amazon's a-price-symbol +
    a-price-whole + a-price-fraction spans, with text separator=" ") must
    canonicalise to ``$329.99`` so the standard price regex captures the
    full price including cents.

    The pattern requires whitespace AFTER the ``$`` — i.e. the symbol must
    be in its own span, separated from the dollar digits. This is the
    Amazon shape; sites that emit ``$329`` together aren't transformed."""
    raw = "Bose 700 Headphones $ 329 99 Other $ 1,299 50 done"
    out = universal_ai._canonicalize_prices(raw)
    assert "$329.99" in out, out
    assert "$1,299.50" in out, out

    # Normal joined markup is left untouched.
    assert universal_ai._canonicalize_prices("Already $329.99 there") == \
        "Already $329.99 there"

    # ``$5 70`` (no space after $) is NOT split-shape; left alone — the
    # whitespace-after-$ requirement keeps random digit pairs from being
    # mistaken for prices. Pinned so the behaviour is explicit.
    assert universal_ai._canonicalize_prices("$5 70") == "$5 70"


def test_extract_handles_amazon_split_price_markup() -> None:
    """Amazon-shape product cards yield real prices through the
    canonicaliser — both via a-offscreen accessibility text AND via the
    a-price-whole + a-price-fraction split markup."""
    html = (FIXTURE_DIR / "amazon_split_price.html").read_text(encoding="utf-8")
    cands = universal_ai._extract_candidates(
        html, base_url="https://www.amazon.com/s?k=bose+700"
    )

    by_href = {c["href"]: c for c in cands}

    nc700 = by_href["https://www.amazon.com/dp/B07Q9MJKBV"]
    # Either a-offscreen OR split markup yields $329.99.
    assert any("329.99" in p for p in nc700["price_hints"]), (
        f"NC700 missing $329.99 in {nc700['price_hints']}"
    )

    qc_ultra = by_href["https://www.amazon.com/dp/B0CCZ265TF"]
    # Split-only card (no a-offscreen) — the canonicaliser is the only
    # path that surfaces the full price.
    assert any("429.00" in p for p in qc_ultra["price_hints"]), (
        f"QC Ultra missing $429.00 in {qc_ultra['price_hints']}"
    )

    sony = by_href["https://www.amazon.com/dp/B0BXZ9MJKB"]
    assert any("398.00" in p for p in sony["price_hints"])


def test_amazon_card_primary_price_skips_strikethrough_and_used() -> None:
    """Phase 19: Amazon's multi-price cards yield ONLY the buy-now price.

    The 2026-05-04 Breville run recorded BES876BSS Impress at $489.50 —
    the "From: $489.95" used-condition price — when the actual buy-now
    price was $649.95. Pin the fix: the new Amazon-specific helper picks
    the first non-strikethrough <span class="a-price"> > a-offscreen, so
    "List: $799.95" (strikethrough) and "From: $489.95" (a separate
    sub-link, not inside an a-price) are ignored.
    """
    html = (FIXTURE_DIR / "amazon-breville-multi-price.html").read_text(encoding="utf-8")
    cands = universal_ai._extract_candidates(
        html, base_url="https://www.amazon.com/s?k=breville+barista+express"
    )

    by_href_substr = {}
    for c in cands:
        for asin in ("B0BBYNPV33", "B00CH9QWOU", "B00DS4767K"):
            if asin in c["href"] and "/dp/" in c["href"]:
                by_href_substr[asin] = c

    impress = by_href_substr["B0BBYNPV33"]
    # Buy-now price wins; strikethrough $799.95 and "From: $489.95" are out.
    assert impress["price_hints"] == ["$649.95"], impress["price_hints"]

    bes870xl = by_href_substr["B00CH9QWOU"]
    assert bes870xl["price_hints"] == ["$549.95"], bes870xl["price_hints"]

    # Subscribe-and-Save price ($445.83) is also a span.a-price — but it's
    # the SECOND one in the card, so the helper still picks the first
    # (the buy-now $469.29).
    sesame = by_href_substr["B00DS4767K"]
    assert sesame["price_hints"] == ["$469.29"], sesame["price_hints"]


def test_amazon_card_primary_price_picks_buy_now_over_list_strikethrough() -> None:
    """Real Amazon body: card with a "$180.28" buy-now + "List: $219.99"
    strikethrough must yield only the buy-now in price_hints.

    This pins the 2026-05-09 paintball-pistol diagnosis: the umarex T4E
    card's $180.28 was the actual displayed buy-now (Amazon's dynamic
    pricing for that AlterLab session), and the strikethrough $219.99
    was correctly skipped. Captured live so the helper continues to
    handle Amazon's `<span class="a-price"> ... <span class="a-price
    a-text-price" data-a-strike="true"> ... </span>` ordering.
    """
    html = (
        FIXTURE_DIR / "amazon-umarex-t4e-walther-2026-05-09.html"
    ).read_text(encoding="utf-8")
    cands = universal_ai._extract_candidates(
        html, base_url="https://www.amazon.com/s?k=umarex+t4e+walther+ppq+.43"
    )

    # The Best-Seller B076DFQYGH card is the rank-1 result in this fixture.
    target = None
    for c in cands:
        if "B076DFQYGH" in c["href"] and "/dp/" in c["href"]:
            target = c
            break
    assert target is not None, "B076DFQYGH card not extracted from Amazon fixture"

    # Buy-now $180.28 wins; List: $219.99 (data-a-strike="true") must be ignored.
    assert target["price_hints"] == ["$180.28"], target["price_hints"]
    # Defensive: $219.99 must not leak into the hint list under any path.
    assert "$219.99" not in target["price_hints"]


def test_amazon_card_primary_price_returns_none_outside_card() -> None:
    """The helper bails when no s-result-item ancestor exists, so the
    generic regex fallback runs instead. Sites that aren't Amazon-shaped
    keep their existing extraction behavior even if the host happens to be
    amazon.<tld> (defensive — a future amazon subpage could ship without
    s-result-item containers, and we don't want to silently drop prices)."""
    from selectolax.parser import HTMLParser
    html = '<div><a href="/dp/X"><h2>Some Title</h2></a><p>$25.00</p></div>'
    tree = HTMLParser(html)
    a = tree.css_first("a")
    assert universal_ai._amazon_card_primary_price(a) is None


def test_fetch_extracts_listings_offline_from_target_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end with stubbed LLM: real Target HTML → ≥3 Listings out.

    Satisfies the Phase 15 done-when criterion ("≥3 listings each from 4 of
    6 real-vendor fixtures via the offline test"). The stubbed LLM mirrors
    what a real Haiku call would do: pick the first 5 candidates and emit
    them with the price hint that was attached.
    """
    html = TARGET_FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (html, 200, "stub"),
    )

    def _llm(**kwargs: object) -> LLMResponse:
        import json as _json
        payload = _json.loads(kwargs["messages"][0].content)  # type: ignore[index]
        decisions = []
        for c in payload[:5]:
            if not c["price_hints"]:
                continue
            # Parse first price hint — it's "$XX.XX" or "$X,XXX.XX".
            price_str = c["price_hints"][0].lstrip("$").replace(",", "")
            try:
                price = float(price_str)
            except ValueError:
                continue
            decisions.append({
                "idx": c["idx"],
                "title": c["anchor_text"] or "Untitled",
                "price_usd": price,
                "condition": "new",
            })
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text=_json.dumps({"listings": decisions}),
            input_tokens=500, output_tokens=200,
        )

    monkeypatch.setattr(universal_ai, "call_llm", _llm)

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": TARGET_URL})
    results = universal_ai.fetch(query)

    assert len(results) >= 3, f"expected ≥3 Listings, got {len(results)}"
    # All emitted Listings must carry verbatim candidate URLs (no LLM URL
    # synthesis is structurally possible).
    for r in results:
        assert r.url.startswith("https://www.target.com/")
        assert r.unit_price_usd > 0
        assert r.attrs.get("extractor") == "anchor_llm"


# --- Phase 19b: foreign currency handling ------------------------------------


def test_strip_foreign_currencies_removes_eur_amounts() -> None:
    """AlterLab's European exit IPs cause Amazon to embed EUR prices.
    _strip_foreign_currencies must remove them so the LLM can't confuse
    EUR amounts for USD."""
    raw = "See options No featured offers available EUR\u20ac490.07 (16 used & new offers)"
    cleaned = universal_ai._strip_foreign_currencies(raw)
    assert "490.07" not in cleaned
    assert "EUR" not in cleaned
    # Non-currency text preserved.
    assert "See options" in cleaned
    assert "16 used" in cleaned

    # Bare € symbol.
    assert "329" not in universal_ai._strip_foreign_currencies("Price \u20ac329.99 only")

    # GBP.
    assert "249" not in universal_ai._strip_foreign_currencies("Only \u00a3249.00 left")

    # USD amounts must NOT be stripped.
    assert "549.95" in universal_ai._strip_foreign_currencies("Buy now $549.95 today")


def test_foreign_price_to_usd_converts_eur() -> None:
    """EUR→USD conversion produces a plausible USD price via live rates."""
    res = universal_ai._foreign_price_to_usd("EUR\u20ac490.07 (16 offers)")
    assert res is not None
    usd, code = res
    # EUR 490.07 at any reasonable EUR→USD rate (0.95–1.30) → $465–$637.
    assert 450.0 < usd < 650.0, f"unexpected converted price: {usd}"
    assert code == "EUR"


def test_foreign_price_to_usd_handles_comma_decimal() -> None:
    """European locales use comma as decimal separator: EUR€490,07."""
    res = universal_ai._foreign_price_to_usd("EUR\u20ac490,07")
    assert res is not None
    usd, code = res
    assert 450.0 < usd < 650.0
    assert code == "EUR"


def test_foreign_price_to_usd_returns_none_for_usd() -> None:
    """USD text must NOT match the foreign price extractor."""
    assert universal_ai._foreign_price_to_usd("$549.95 buy now") is None
    assert universal_ai._foreign_price_to_usd("No currency at all") is None


def test_amazon_alterlab_eur_cards_get_approximate_usd_prices() -> None:
    """Phase 19b: AlterLab-rendered Amazon body shows 'See options' cards
    with EUR prices instead of USD. The extractor must convert EUR→USD
    and produce approximate price hints rather than dropping the listings.

    Pinned against the real AlterLab-captured body from 2026-05-04."""
    fixture = FIXTURE_DIR / "amazon-breville-alterlab-2026-05-04.html"
    if not fixture.exists():
        pytest.skip("AlterLab Amazon fixture not available")

    html = fixture.read_text(encoding="utf-8")
    cands = universal_ai._extract_candidates(
        html, base_url="https://www.amazon.com/s?k=breville+barista+express"
    )

    # Find the BES876BSS Impress card (ASIN B0BBYNPV33) — it had EUR€490.07
    # in the AlterLab body.
    impress_cands = [
        c for c in cands
        if "B0BBYNPV33" in c["href"] and "/dp/" in c["href"]
    ]
    assert impress_cands, "BES876BSS Impress not found in candidates"

    # At least one of the collapsed candidates should have a converted price.
    prices_for_impress = []
    for c in impress_cands:
        prices_for_impress.extend(c["price_hints"])

    if prices_for_impress:
        # EUR€490.07 at live rate → plausible USD range.
        price_val = float(prices_for_impress[0].lstrip("$").replace(",", ""))
        assert 450.0 < price_val < 650.0, (
            f"converted price {price_val} outside plausible range for EUR€490.07"
        )

    # Context must NOT contain raw EUR currency amounts (stripped for LLM
    # safety).  Our own "[price approx. from EUR]" tag is fine — it's the
    # raw "EUR\xa0490.07" pattern we need to block.
    import re as _re
    for c in impress_cands:
        assert not _re.search(r"EUR[\s\xa0]*\d", c["context"]), (
            f"Raw EUR price leaked into context: {c['context'][:100]}"
        )


# --- Phase 19 / ADR-049: Tier 1.5 single-product detail extractor ----------

DETAIL_FIXTURE = FIXTURE_DIR / "detail-single-sku-synthetic.html"
# A real-shaped parked amd-epyc-9255 URL: not a search/category URL, last
# path segment is a long hyphenated SKU slug → URL-shape heuristic = detail.
DETAIL_URL = "https://www.sabrepc.com/100-000000694-AMD-S137839588"


def _detail_html() -> str:
    return DETAIL_FIXTURE.read_text(encoding="utf-8")


def test_strip_to_main_text_drops_chrome_keeps_price() -> None:
    """The strip pass removes script/nav/header/footer and the fake
    in-script price, but keeps the visible title + the real $2,335.00."""
    text = universal_ai._strip_to_main_text(_detail_html())
    assert "AMD EPYC 9255 24-Core 3.25GHz Processor" in text
    assert "$2,335.00" in text
    assert "In Stock" in text
    # Chrome / script noise must be gone.
    assert "9999999" not in text, "in-<script> fake price leaked"
    assert "My Account" not in text, "<header> nav leaked"
    assert "All RMA Request" not in text, "<nav> junk leaked"
    assert "Return Policy" not in text, "<footer> leaked"


def test_price_in_text_verbatim_guard() -> None:
    """The anti-hallucination guard tolerates print-format variation but
    rejects a price that is not in the fetched bytes (ADR-001)."""
    body = "Our Price: $2,335.00 today only. SKU 100-000000694."
    assert universal_ai._price_in_text(2335.0, body)
    assert universal_ai._price_in_text(2335.00, body)
    # Comma-free / cents-free printed forms normalise the same way.
    assert universal_ai._price_in_text(2335, "flat 2335 dollars")
    assert universal_ai._price_in_text(2335.0, "now $2335")
    # Fabricated price → dropped.
    assert not universal_ai._price_in_text(4567.89, body)
    assert not universal_ai._price_in_text(2336.0, body)


def test_resolve_detail_mode_gating() -> None:
    """Explicit page_type wins; URL-shape heuristic is the fallback."""
    q_detail = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": DETAIL_URL, "page_type": "detail"},
    )
    q_search = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": DETAIL_URL, "page_type": "search"},
    )
    q_auto = AdapterQuery(
        source_id="universal_ai_search", extra={"url": DETAIL_URL},
    )
    q_search_url = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": "https://www.cdw.com/search/?key=amd+epyc+9255"},
    )
    assert universal_ai._resolve_detail_mode(q_detail, DETAIL_URL) == "detail"
    assert universal_ai._resolve_detail_mode(q_search, DETAIL_URL) == "search"
    assert universal_ai._resolve_detail_mode(q_auto, DETAIL_URL) == "auto"
    assert (
        universal_ai._resolve_detail_mode(
            q_search_url, "https://www.cdw.com/search/?key=amd+epyc+9255"
        )
        == "search"
    )


def test_tier15_emits_single_listing_with_source_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """page_type:detail → one Listing whose URL is the verbatim source URL,
    extractor tagged detail_llm, price taken from the body."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (_detail_html(), 200, "stub"),
    )
    monkeypatch.setattr(universal_ai, "call_llm", _stub_llm_response(
        '{"found": true, "title": "AMD EPYC 9255 24-Core 3.25GHz Processor", '
        '"price_usd": 2335.00, "condition": "new", "in_stock": true, '
        '"pack_size": 1}'
    ))

    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": DETAIL_URL, "page_type": "detail"},
    )
    results = universal_ai.fetch(query)

    assert len(results) == 1
    r = results[0]
    assert r.url == DETAIL_URL  # never LLM-produced
    assert r.unit_price_usd == 2335.00
    assert r.condition == "new"
    assert r.attrs["extractor"] == "detail_llm"
    assert r.attrs["vendor_host"] == "www.sabrepc.com"
    assert r.quantity_available is None  # in_stock True → unknown qty
    assert universal_ai.LAST_RUN_USAGE is not None
    assert universal_ai.LAST_RUN_USAGE["step"] == "universal_ai_search"


def test_tier15_out_of_stock_sets_quantity_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """in_stock:false → quantity_available 0 so the in_stock filter
    (reject when qty <= 0) can drop it downstream."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (_detail_html(), 200, "stub"),
    )
    monkeypatch.setattr(universal_ai, "call_llm", _stub_llm_response(
        '{"found": true, "title": "AMD EPYC 9255", "price_usd": 2335, '
        '"condition": "new", "in_stock": false, "pack_size": 1}'
    ))
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": DETAIL_URL, "page_type": "detail"},
    )
    results = universal_ai.fetch(query)
    assert len(results) == 1
    assert results[0].quantity_available == 0


def test_tier15_drops_fabricated_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """A price the model returns that is NOT verbatim in the fetched body
    is dropped — the guard is stricter than the anchor tier (ADR-001)."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (_detail_html(), 200, "stub"),
    )
    monkeypatch.setattr(universal_ai, "call_llm", _stub_llm_response(
        '{"found": true, "title": "AMD EPYC 9255", "price_usd": 4567.89, '
        '"condition": "new", "in_stock": true, "pack_size": 1}'
    ))
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": DETAIL_URL, "page_type": "detail"},
    )
    assert universal_ai.fetch(query) == []


def test_tier15_found_false_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (_detail_html(), 200, "stub"),
    )
    monkeypatch.setattr(
        universal_ai, "call_llm", _stub_llm_response('{"found": false}')
    )
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": DETAIL_URL, "page_type": "detail"},
    )
    assert universal_ai.fetch(query) == []


def test_tier15_explicit_detail_does_not_fall_through_to_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit page_type:detail + Tier 1.5 finding nothing must NOT burn a
    second (anchor-tier) LLM call — the page IS one product."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (_detail_html(), 200, "stub"),
    )
    calls: list[str] = []

    def _llm(**kwargs: object) -> LLMResponse:
        calls.append(str(kwargs.get("system", ""))[:40])
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text='{"found": false}', input_tokens=100, output_tokens=10,
        )

    monkeypatch.setattr(universal_ai, "call_llm", _llm)
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": DETAIL_URL, "page_type": "detail"},
    )
    assert universal_ai.fetch(query) == []
    assert len(calls) == 1, f"expected exactly 1 LLM call, got {len(calls)}"


def test_tier15_auto_mode_falls_through_to_anchor_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When page_type is ABSENT (URL-shape heuristic only), a Tier 1.5 miss
    must fall through to the anchor tier so a mis-classified search/category
    page is never regressed."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (_detail_html(), 200, "stub"),
    )
    state = {"n": 0}

    def _llm(**kwargs: object) -> LLMResponse:
        import json as _json
        state["n"] += 1
        if state["n"] == 1:
            # Tier 1.5 detail call — find nothing.
            return LLMResponse(
                provider="anthropic", model="claude-haiku-4-5",
                text='{"found": false}', input_tokens=100, output_tokens=10,
            )
        # Anchor tier call — pick the first candidate that has a price hint.
        payload = _json.loads(kwargs["messages"][0].content)  # type: ignore[index]
        for c in payload:
            if c["price_hints"]:
                price = float(c["price_hints"][0].lstrip("$").replace(",", ""))
                return LLMResponse(
                    provider="anthropic", model="claude-haiku-4-5",
                    text=_json.dumps({"listings": [{
                        "idx": c["idx"], "title": c["anchor_text"] or "x",
                        "price_usd": price, "condition": "new",
                    }]}),
                    input_tokens=200, output_tokens=40,
                )
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text='{"listings": []}', input_tokens=200, output_tokens=10,
        )

    monkeypatch.setattr(universal_ai, "call_llm", _llm)
    query = AdapterQuery(source_id="universal_ai_search", extra={"url": DETAIL_URL})
    results = universal_ai.fetch(query)

    assert state["n"] == 3, (
        "auto mode must fall through to the search union "
        "(detail + anchor + full-HTML = 3 LLM calls)"
    )
    assert len(results) >= 1
    assert all(
        r.attrs.get("extractor") in ("anchor_llm", "full_html_llm")
        for r in results
    )


# --- Phase 19 / ADR-049: real captured detail-page fixtures ----------------
#
# Bodies captured 2026-05-17 via `probe-url --render --detail` (AlterLab
# rendered). Like the Phase 15 real-vendor fixtures these are large and pin
# the DETERMINISTIC half of Tier 1.5 (strip + verbatim guard) against real
# DOM density — the LLM call itself is never exercised here (non-deterministic
# + needs the network). They are the regression net for the form-strip fix.

_REAL_DETAIL_EXTRACTS = [
    ("sabrepc-epyc9255-2026-05-17.html", 2523.20),
    ("wiredzone-epyc9255-2026-05-17.html", 2070.0),
    ("itcreations-epyc9255-2026-05-17.html", 2795.0),
    ("newegg-epyc9255-2026-05-17.html", 3202.50),
]
_REAL_DETAIL_BOTWALLS = [
    "serversupply-epyc9255-2026-05-17.html",
    "centralcomputer-epyc9255-2026-05-17.html",
]


@pytest.mark.parametrize("fixture,price", _REAL_DETAIL_EXTRACTS)
def test_strip_real_detail_fixture_exposes_verbatim_price(
    fixture: str, price: float
) -> None:
    """Each real EPYC-9255 detail body, after stripping, must still contain
    the price so the verbatim guard passes. Wiredzone is the regression
    canary for the form-strip fix (its price lives inside the Odoo
    add-to-cart <form>; decomposing <form> would delete it)."""
    html = (FIXTURE_DIR / fixture).read_text(encoding="utf-8")
    text = universal_ai._strip_to_main_text(html)
    assert len(text) > 200, f"{fixture}: stripped body suspiciously small"
    assert universal_ai._price_in_text(price, text), (
        f"{fixture}: price {price} not verbatim in stripped text — "
        f"Tier 1.5 would (correctly) drop a real listing"
    )


@pytest.mark.parametrize("fixture", _REAL_DETAIL_BOTWALLS)
def test_strip_real_botwall_fixture_is_barren(fixture: str) -> None:
    """ServerSupply / CentralComputer hit a bot wall AlterLab only partially
    defeats (ADR-049: Tier 1.5 fixes extraction, not fetch reachability).
    Pin them as barren so a future rendering improvement surfaces as a diff
    rather than silently changing behaviour."""
    import re as _re

    html = (FIXTURE_DIR / fixture).read_text(encoding="utf-8")
    text = universal_ai._strip_to_main_text(html)
    assert _re.search(r"\$\s?\d", text) is None, (
        f"{fixture}: now renders a $ price — promote it out of "
        f"sources_pending and add it to _REAL_DETAIL_EXTRACTS"
    )


def test_form_element_is_not_stripped_keeps_price() -> None:
    """Regression guard for the ADR-049 form-strip bug: many storefronts
    (Odoo/Wiredzone) put the price + Add-to-Cart inside the product <form>.
    _strip_to_main_text must NOT decompose <form>."""
    html = (
        "<html><body><nav>Menu Cart</nav>"
        "<main><h1>AMD EPYC 9255</h1>"
        '<form action="/shop/cart/update">'
        '<span class="oe_currency_value">$2,070.00</span>'
        "<button>Add to Cart</button></form></main>"
        "<footer>Contact</footer></body></html>"
    )
    text = universal_ai._strip_to_main_text(html)
    assert "$2,070.00" in text
    assert universal_ai._price_in_text(2070.0, text)
    assert "Menu Cart" not in text  # <nav> still stripped
    assert "Contact" not in text  # <footer> still stripped


def test_fetch_tier15_wiredzone_fixture_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full path on a REAL captured body: page_type:detail + stubbed LLM
    (returning the price that is verbatim in the stripped Wiredzone text)
    → exactly one Listing carrying the verbatim source URL."""
    html = (
        FIXTURE_DIR / "wiredzone-epyc9255-2026-05-17.html"
    ).read_text(encoding="utf-8")
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (html, 200, "stub"),
    )
    monkeypatch.setattr(universal_ai, "call_llm", _stub_llm_response(
        '{"found": true, "title": "AMD 100-000000694 EPYC 9255 24-Core", '
        '"price_usd": 2070.00, "condition": "new", "in_stock": false, '
        '"pack_size": 1}'
    ))
    wz_url = (
        "https://www.wiredzone.com/shop/product/10032075-amd-100-000000694-"
        "epyc-9255-3-20ghz-24-core-processor-5th-generation-turin-14772"
    )
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": wz_url, "page_type": "detail"},
    )
    results = universal_ai.fetch(query)
    assert len(results) == 1
    assert results[0].url == wz_url
    assert results[0].unit_price_usd == 2070.00
    assert results[0].attrs["extractor"] == "detail_llm"
    assert results[0].quantity_available == 0  # in_stock false


def test_parse_pack_extracts_multi_packs() -> None:
    """_parse_pack decodes module counts and unit prices for multi-pack items."""
    is_kit, count, unit_p, kit_p = universal_ai._parse_pack("Aufschnitt Jerky 2-pack", 14.00)
    assert is_kit is True
    assert count == 2
    assert unit_p == 7.00
    assert kit_p == 14.00

    is_kit, count, unit_p, kit_p = universal_ai._parse_pack("Aufschnitt Jerky 6 count", 42.00)
    assert is_kit is True
    assert count == 6
    assert unit_p == 7.00
    assert kit_p == 42.00

    # LLM explicit pack size overrides title regex when > 1.
    is_kit, count, unit_p, kit_p = universal_ai._parse_pack("Generic Title", 30.00, llm_pack_size=5)
    assert is_kit is True
    assert count == 5
    assert unit_p == 6.00
    assert kit_p == 30.00


def test_parse_pack_accessory_bundle_guard() -> None:
    """A title containing 'Bundle' (no homogeneous-multi-pack pattern) downgrades
    LLM-claimed pack_size > 1 back to 1.

    Regression for the 2026-05-20 Best Buy sony-wh-1000xm5 case where Tier 2's
    LLM tagged accessory bundles ("WH-1000XM5 ... + Wood Headphone Stand Bundle")
    as pack_size=2; ``_parse_pack`` halved the bundle price and reported the
    headphone at $135 instead of the actual $269.99.
    """
    title = "Sony - WH-1000XM5 Wireless Noise Canceling Headphones, Silver + Wood Headphone Stand Bundle"
    is_kit, count, unit_p, kit_p = universal_ai._parse_pack(title, 269.99, llm_pack_size=2)
    assert is_kit is False
    assert count == 1
    assert unit_p == 269.99
    assert kit_p is None

    # An explicit homogeneous multi-pack still wins even when "bundle" is in title.
    is_kit, count, unit_p, kit_p = universal_ai._parse_pack(
        "WidgetCorp Bundle: 4-pack", 80.00, llm_pack_size=4
    )
    assert is_kit is True
    assert count == 4
    assert unit_p == 20.00
    assert kit_p == 80.00


def test_alterlab_options_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AdapterQuery has alterlab_options, they are propagated through fetch
    and correctly serialized in the AlterLab API payload."""
    monkeypatch.setenv("ALTERLAB_API_KEY", "test-key-opts")

    captured: dict[str, Any] = {}

    class _StubClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            json: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> Any:
            captured["json"] = json
            captured["headers"] = headers

            class _Resp:
                status_code = 200
                def raise_for_status(self) -> None:
                    return None
                def json(self) -> dict[str, Any]:
                    # Return empty listing array via JSON-LD stub to terminate early without LLM call
                    return {
                        "status_code": 200,
                        "content": {"html": "<html><body>" + ("stub ok product " * 400) + "</body></html>"},
                    }
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _StubClient)

    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={
            "url": "https://example.com/products",
            "alterlab_options": {
                "country": "us",
                "min_tier": 3,
                "wait_condition": "networkidle",
                "render_js": True,
            }
        }
    )

    # Make fetch execute the cascade
    universal_ai.fetch(query)

    assert captured["headers"]["X-API-Key"] == "test-key-opts"
    assert captured["json"]["url"] == "https://example.com/products"
    # ADR-071: documented nested wire shape — flat country/min_tier are mapped to
    # location.country / cost_controls.max_tier and must NOT appear at top level.
    assert captured["json"]["location"] == {"country": "us"}
    assert captured["json"]["cost_controls"] == {"max_tier": "3"}
    assert "country" not in captured["json"]
    assert "min_tier" not in captured["json"]
    assert captured["json"]["advanced"]["wait_condition"] == "networkidle"
    # ADR-071: wait_for is the broken legacy param and must never reach the wire.
    assert "wait_for" not in captured["json"]["advanced"]
    assert captured["json"]["advanced"]["render_js"] is True


def test_alterlab_options_propagation_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    """When AdapterQuery has alterlab_options nested in an 'extra' dict, they are still propagated correctly."""
    monkeypatch.setenv("ALTERLAB_API_KEY", "test-key-opts-nested")

    captured: dict[str, Any] = {}

    class _StubClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            json: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
        ) -> Any:
            captured["json"] = json
            captured["headers"] = headers

            class _Resp:
                status_code = 200
                def raise_for_status(self) -> None:
                    return None
                def json(self) -> dict[str, Any]:
                    return {
                        "status_code": 200,
                        "content": {"html": "<html><body>" + ("stub ok product " * 400) + "</body></html>"},
                    }
            return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", _StubClient)

    # Nesting extra.alterlab_options as generated by Pydantic models when loaded from profile.yaml
    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={
            "url": "https://example.com/products-nested",
            "extra": {
                "alterlab_options": {
                    "country": "us",
                    "min_tier": 3,
                    # Legacy CSS-selector wait_for must be migrated to
                    # wait_condition:networkidle and never sent verbatim (ADR-071).
                    "wait_for": ".product-grid-nested",
                    "render_js": True,
                }
            }
        }
    )

    universal_ai.fetch(query)

    assert captured["headers"]["X-API-Key"] == "test-key-opts-nested"
    assert captured["json"]["url"] == "https://example.com/products-nested"
    assert captured["json"]["location"] == {"country": "us"}
    assert captured["json"]["cost_controls"] == {"max_tier": "3"}
    assert "country" not in captured["json"]
    assert "min_tier" not in captured["json"]
    assert captured["json"]["advanced"]["wait_condition"] == "networkidle"
    assert "wait_for" not in captured["json"]["advanced"]
    assert captured["json"]["advanced"]["render_js"] is True


# --- ADR-071: retry-on-weak-render + bounded escalation --------------------


def test_weak_render_reason_detects_failures() -> None:
    """The cheap weak-render predicate flags empty / short / challenge / 4xx."""
    assert universal_ai._weak_render_reason("", 200)  # empty
    assert universal_ai._weak_render_reason("x" * 100, 200)  # too short
    assert universal_ai._weak_render_reason("<html>" + "y" * 5000, 503)  # bad status
    challenge = "<html><body>Just a moment... checking your browser</body></html>" + "z" * 5000
    assert universal_ai._weak_render_reason(challenge, 200)
    target_stub = "<html>" + "ok " * 100000 + "There was a temporary issue" + " ok" * 100
    assert universal_ai._weak_render_reason(target_stub, 200)
    # A healthy big body passes.
    assert universal_ai._weak_render_reason("<html>" + "real product page " * 5000, 200) is None


def test_escalation_ladder_dedupes_strong_options() -> None:
    """The ladder skips a rung that would re-send an already-present option, and
    escalates to tier 4 via the documented cost_controls.max_tier path (ADR-071)."""
    # networkidle present + already tier 4 → no extra rung.
    ladder = universal_ai._escalation_ladder({"min_tier": 4, "wait_condition": "networkidle"})
    assert len(ladder) == 1
    # networkidle present, tier 3 → one extra rung that bumps to tier 4.
    ladder_t4 = universal_ai._escalation_ladder({"min_tier": 3, "wait_condition": "networkidle"})
    assert len(ladder_t4) == 2
    assert ladder_t4[1]["min_tier"] == 4
    # Plain options → 3-rung ladder (base, +networkidle, +tier4).
    ladder2 = universal_ai._escalation_ladder({"country": "us", "min_tier": 3})
    assert len(ladder2) == 3
    assert ladder2[1]["wait_condition"] == "networkidle"
    assert ladder2[2]["min_tier"] == 4
    assert ladder2[2]["wait_condition"] == "networkidle"  # carried forward


def _stub_escalation_fetches(
    monkeypatch: pytest.MonkeyPatch, bodies: list[tuple[str, int]]
) -> list[dict[str, Any] | None]:
    """Make ``_fetch_html_with_retry`` return ``bodies`` in order; record opts."""
    monkeypatch.setenv("ALTERLAB_API_KEY", "test-escalation-key")
    seen_opts: list[dict[str, Any] | None] = []
    calls = {"i": 0}

    def _fake(url: str, alterlab_options: dict[str, Any] | None = None) -> tuple[str, int, str]:
        seen_opts.append(alterlab_options)
        html, status = bodies[min(calls["i"], len(bodies) - 1)]
        calls["i"] += 1
        return html, status, "alterlab"

    monkeypatch.setattr(universal_ai, "_fetch_html_with_retry", _fake)
    return seen_opts


def test_escalation_recovers_on_second_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """A weak first render escalates; a good second render is returned."""
    good = "<html>" + "real product page " * 5000
    seen = _stub_escalation_fetches(monkeypatch, [("Just a moment..." + "x" * 5000, 200), (good, 200)])
    html, status, fetcher, attempts, degraded = universal_ai._fetch_with_escalation(
        "https://hard.example/p", {"country": "us", "min_tier": 3}
    )
    assert html == good
    assert len(attempts) == 2  # escalated exactly once
    # A recovered AlterLab render is NOT degraded (the stub fetcher is alterlab).
    assert degraded is False
    # Attempt 2 added networkidle.
    assert seen[1] is not None and seen[1]["wait_condition"] == "networkidle"


def test_no_escalation_on_healthy_first_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: a good first render costs exactly one fetch (no escalation)."""
    good = "<html>" + "real product page " * 5000
    seen = _stub_escalation_fetches(monkeypatch, [(good, 200)])
    html, status, fetcher, attempts, degraded = universal_ai._fetch_with_escalation(
        "https://easy.example/p", {"country": "us", "min_tier": 3}
    )
    assert html == good
    assert len(attempts) == 1
    assert len(seen) == 1
    assert degraded is False


def test_escalation_returns_best_effort_when_all_weak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rung weak → return the largest body seen (3-rung ladder; ADR-071)."""
    seen = _stub_escalation_fetches(
        monkeypatch,
        [
            ("Just a moment" + "x" * 3000, 200),
            ("Just a moment" + "y" * 9000, 200),
            ("Just a moment" + "z" * 5000, 200),
        ],
    )
    html, status, fetcher, attempts, degraded = universal_ai._fetch_with_escalation(
        "https://walled.example/p", {"country": "us", "min_tier": 3}
    )
    assert len(attempts) == 3  # exhausted the ladder (base, networkidle, tier4)
    assert "y" * 9000 in html  # largest body kept
    # Every rung weak → AlterLab is degraded for breaker purposes (ADR-078).
    assert degraded is True
    # Final rung escalates to tier 4 via the documented cost_controls.max_tier path.
    assert seen[-1] is not None and seen[-1]["min_tier"] == 4


# --- ADR-078 (R1): AlterLab 5xx retry before the curl_cffi fallback --------


class _StubAlterlabResp:
    """A stub of httpx's response object for _fetch_via_alterlab tests."""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._payload


def _install_stub_alterlab_client(
    monkeypatch: pytest.MonkeyPatch, statuses: list[int]
) -> dict[str, int]:
    """Install an httpx.Client stub whose POST returns ``statuses`` in order
    (a 200 carries a usable HTML envelope). Returns a {"posts": n} counter."""
    counter = {"posts": 0}

    class _StubClient:
        def __init__(self, **_kw: object) -> None:
            pass

        def __enter__(self) -> "_StubClient":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, _url: str, **_kw: object) -> _StubAlterlabResp:
            i = min(counter["posts"], len(statuses) - 1)
            code = statuses[i]
            counter["posts"] += 1
            if code == 200:
                return _StubAlterlabResp(200, {
                    "status_code": 200,
                    "content": {"html": "<html><body>recovered</body></html>"},
                })
            return _StubAlterlabResp(code, {"detail": f"HTTP {code}"})

    import httpx
    monkeypatch.setattr(httpx, "Client", _StubClient)
    return counter


def test_alterlab_retries_on_5xx_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient 503/504 from the AlterLab API is retried (not abandoned to
    curl_cffi); a subsequent 200 is returned. ADR-078 (R1)."""
    monkeypatch.setattr(universal_ai.time, "sleep", lambda _s: None)
    counter = _install_stub_alterlab_client(monkeypatch, [503, 504, 200])

    html, status, fetcher = universal_ai._fetch_via_alterlab(
        "https://hard.example/p", "test-key"
    )
    assert fetcher == "alterlab"
    assert status == 200
    assert "recovered" in html
    assert counter["posts"] == 3  # two retries then success


def test_alterlab_5xx_raises_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """A persistent 5xx raises after the bounded retries (caller then falls
    through to curl_cffi). ADR-078 (R1)."""
    import httpx
    monkeypatch.setattr(universal_ai.time, "sleep", lambda _s: None)
    counter = _install_stub_alterlab_client(monkeypatch, [500])

    with pytest.raises(httpx.HTTPStatusError):
        universal_ai._fetch_via_alterlab("https://down.example/p", "test-key")
    assert counter["posts"] == universal_ai._ALTERLAB_5XX_MAX_ATTEMPTS


def test_alterlab_4xx_raises_immediately_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 4xx (auth/quota/422) is NOT retried — retrying can't fix it and would
    re-spend the long AlterLab timeout. ADR-078 (R1)."""
    import httpx
    monkeypatch.setattr(universal_ai.time, "sleep", lambda _s: None)
    counter = _install_stub_alterlab_client(monkeypatch, [403])

    with pytest.raises(httpx.HTTPStatusError):
        universal_ai._fetch_via_alterlab("https://auth.example/p", "test-key")
    assert counter["posts"] == 1  # no retry


# --- ADR-083: browser_pool_exhausted 422 is transient, so retry it ---------


def _install_stub_alterlab_client_texts(
    monkeypatch: pytest.MonkeyPatch, items: list[tuple[int, str]]
) -> dict[str, int]:
    """Like ``_install_stub_alterlab_client`` but each non-200 response also
    carries a ``.text`` body (so ``_is_transient_alterlab_422`` can inspect it).
    ``items`` is ``[(status_code, body_text), ...]`` returned in order."""
    counter = {"posts": 0}

    class _Client:
        def __init__(self, **_kw: object) -> None:
            pass

        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, _url: str, **_kw: object) -> _StubAlterlabResp:
            i = min(counter["posts"], len(items) - 1)
            code, text = items[i]
            counter["posts"] += 1
            if code == 200:
                resp = _StubAlterlabResp(200, {
                    "status_code": 200,
                    "content": {"html": "<html><body>recovered</body></html>"},
                })
                resp.text = "ok"  # type: ignore[attr-defined]
                return resp
            resp = _StubAlterlabResp(code, {"detail": text})
            resp.text = text  # type: ignore[attr-defined]
            return resp

    import httpx
    monkeypatch.setattr(httpx, "Client", _Client)
    return counter


def test_alterlab_pool_exhausted_422_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 422 whose body names ``browser_pool_exhausted`` is a transient capacity
    error — retried (not abandoned to curl_cffi) until a 200 arrives. ADR-083."""
    monkeypatch.setattr(universal_ai.time, "sleep", lambda _s: None)
    counter = _install_stub_alterlab_client_texts(
        monkeypatch,
        [(422, '{"error": "browser_pool_exhausted"}'), (200, "")],
    )

    html, status, fetcher = universal_ai._fetch_via_alterlab(
        "https://hard.example/p", "test-key"
    )
    assert fetcher == "alterlab"
    assert "recovered" in html
    assert counter["posts"] == 2  # one retry then success
    assert universal_ai._LAST_ALTERLAB_POOL_EXHAUSTED is True


def test_alterlab_pool_exhausted_422_raises_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persistently pool-exhausted 422 still raises after the bounded retries
    (caller falls through), but the flag is set so the run can label it. ADR-083."""
    import httpx
    monkeypatch.setattr(universal_ai.time, "sleep", lambda _s: None)
    counter = _install_stub_alterlab_client_texts(
        monkeypatch, [(422, "browser_pool_exhausted")]
    )

    with pytest.raises(httpx.HTTPStatusError):
        universal_ai._fetch_via_alterlab("https://busy.example/p", "test-key")
    assert counter["posts"] == universal_ai._ALTERLAB_5XX_MAX_ATTEMPTS
    assert universal_ai._LAST_ALTERLAB_POOL_EXHAUSTED is True


def test_alterlab_non_transient_422_raises_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 422 that is NOT pool-exhaustion (a genuine malformed request) still
    raises on the first attempt — no retry, no pool flag. ADR-083."""
    import httpx
    monkeypatch.setattr(universal_ai.time, "sleep", lambda _s: None)
    counter = _install_stub_alterlab_client_texts(
        monkeypatch, [(422, '{"error": "invalid_url"}')]
    )

    with pytest.raises(httpx.HTTPStatusError):
        universal_ai._fetch_via_alterlab("https://bad.example/p", "test-key")
    assert counter["posts"] == 1  # no retry
    assert universal_ai._LAST_ALTERLAB_POOL_EXHAUSTED is False


# --- ADR-084: fetch() populates LAST_FETCH_DIAGNOSTICS ---------------------


def test_fetch_populates_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch()`` records body_len / fetcher / degraded so the cli source-reason
    classifier can tell a parser gap from a transient failure. ADR-084."""
    # No AlterLab key → _fetch_with_escalation uses the single-fetch path and
    # reports alterlab_degraded=False (the stub fetcher is not a fallback).
    monkeypatch.delenv("ALTERLAB_API_KEY", raising=False)
    body = "<html><body>" + "x" * 80_000 + "</body></html>"
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (body, 200, "stub"),
    )
    monkeypatch.setattr(universal_ai, "call_llm", _stub_llm_response('{"listings": []}'))

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    assert universal_ai.fetch(query) == []

    diag = universal_ai.LAST_FETCH_DIAGNOSTICS
    assert diag is not None
    assert diag["body_len"] == len(body)
    assert diag["final_status"] == 200
    assert diag["final_fetcher"] == "stub"
    assert diag["alterlab_degraded"] is False
    assert diag["alterlab_pool_exhausted"] is False


# --- ADR-078 (R6): per-run circuit breaker + wall-clock budget -------------


@pytest.fixture(autouse=True)
def _reset_breaker() -> Any:
    """Keep breaker module state isolated per test."""
    universal_ai.reset_run_state()
    yield
    universal_ai.reset_run_state()


def test_breaker_opens_after_consecutive_failures() -> None:
    """N consecutive AlterLab-degraded outcomes open the circuit; a healthy one
    resets the counter. ADR-078 (R6)."""
    universal_ai.reset_run_state()
    assert universal_ai._circuit_open is False
    for _ in range(universal_ai._BREAKER_THRESHOLD - 1):
        universal_ai._note_alterlab_outcome(degraded=True)
    assert universal_ai._circuit_open is False
    # A healthy fetch resets the streak.
    universal_ai._note_alterlab_outcome(degraded=False)
    assert universal_ai._consecutive_alterlab_failures == 0
    # Now a full streak opens it.
    for _ in range(universal_ai._BREAKER_THRESHOLD):
        universal_ai._note_alterlab_outcome(degraded=True)
    assert universal_ai._circuit_open is True


def test_fetch_short_circuits_when_breaker_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the circuit open, fetch() skips the source (no fetch attempt) and
    records a skip reason. ADR-078 (R6)."""
    universal_ai.reset_run_state()
    for _ in range(universal_ai._BREAKER_THRESHOLD):
        universal_ai._note_alterlab_outcome(degraded=True)
    assert universal_ai._circuit_open is True

    def _must_not_fetch(*_a: object, **_k: object) -> Any:  # pragma: no cover
        raise AssertionError("fetch must short-circuit when breaker is open")

    monkeypatch.setattr(universal_ai, "_fetch_with_escalation", _must_not_fetch)

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    results = universal_ai.fetch(query)
    assert results == []
    assert universal_ai.LAST_SKIP_REASON is not None
    assert "circuit open" in universal_ai.LAST_SKIP_REASON


def test_fetch_short_circuits_when_budget_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the per-run budget spent, fetch() skips remaining sources. ADR-078 (R6)."""
    universal_ai.reset_run_state()
    # Force the deadline into the past.
    universal_ai._run_deadline = universal_ai.time.monotonic() - 1.0

    def _must_not_fetch(*_a: object, **_k: object) -> Any:  # pragma: no cover
        raise AssertionError("fetch must short-circuit when budget exceeded")

    monkeypatch.setattr(universal_ai, "_fetch_with_escalation", _must_not_fetch)

    query = AdapterQuery(source_id="universal_ai_search", extra={"url": BASE_URL})
    results = universal_ai.fetch(query)
    assert results == []
    assert universal_ai.LAST_SKIP_REASON is not None
    assert "budget" in universal_ai.LAST_SKIP_REASON


# --- ADR-077: recall-first full-HTML search extractor ----------------------
#
# Tests for the search union: JSON-LD ∪ anchor-walker ∪ full-HTML-LLM.
# The synthetic-search-recall-gap.html fixture has 7 products: 2 with prices
# near their anchors (anchor walker finds them), 5 with prices only in a
# separate pricing table (anchor walker misses them, full-HTML LLM finds them).

RECALL_GAP_FIXTURE = FIXTURE_DIR / "synthetic-search-recall-gap.html"
RECALL_GAP_URL = "https://syntheticvendor.example.com/search?q=bose+headphones"


def test_canonical_url_normalises_host_and_trailing_slash() -> None:
    """_canonical_url must lowercase the host and strip trailing slashes."""
    assert universal_ai._canonical_url(
        "https://WWW.Example.COM/Products/Bose-700/"
    ) == "https://www.example.com/Products/Bose-700"
    assert universal_ai._canonical_url(
        "http://example.com/p"
    ) == "http://example.com/p"
    # Query params are preserved (they're part of the parsed URL path-less info).
    u = universal_ai._canonical_url("https://example.com/search?q=bose")
    assert "search" in u  # path is preserved


def test_collect_search_anchors_dedupes_by_canonical() -> None:
    """Every <a href> on the page, deduped by canonical URL."""
    html = RECALL_GAP_FIXTURE.read_text(encoding="utf-8")
    anchors = universal_ai._collect_search_anchors(html, base_url=RECALL_GAP_URL)
    # 7 product links + 5 non-product (home, about, contact, privacy, terms)
    canonicals = [a["canonical"] for a in anchors]
    assert len(canonicals) == len(set(canonicals)), "duplicates in search anchors"
    product_anchors = [a for a in anchors if "/products/" in a["href_abs"]]
    assert len(product_anchors) == 7, f"expected 7 product anchors, got {len(product_anchors)}"


def test_chunk_text_single_chunk_when_short() -> None:
    """Text shorter than max_chars should yield a single chunk."""
    text = "Hello world " * 10
    chunks = universal_ai._chunk_text(text, max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_text_splits_with_overlap() -> None:
    """Long text should be split into overlapping chunks."""
    text = "A" * 2500
    chunks = universal_ai._chunk_text(text, max_chars=1000, overlap=200)
    assert len(chunks) >= 3
    # Each chunk should be <= max_chars
    assert all(len(c) <= 1000 for c in chunks)
    # The full text should be recoverable (modulo overlap).
    assert "".join(chunks) != text  # overlapping, so concatenation is longer
    assert chunks[0][:100] == text[:100]
    assert chunks[-1][-100:] == text[-100:]


def test_union_by_canonical_preserves_first_seen() -> None:
    """_union_by_canonical keeps the first listing per canonical URL."""
    from datetime import UTC, datetime

    from product_search.models import Listing

    now = datetime.now(tz=UTC)
    base = dict(
        source="universal_ai_search",
        fetched_at=now, brand=None, mpn=None,
        condition="new", is_kit=False, kit_module_count=1,
        quantity_available=None, seller_name="host",
        seller_rating_pct=None, seller_feedback_count=None,
        ship_from_country=None,
    )
    jsonld = [Listing(url="https://ex.com/p/1", title="P1 jsonld",
                      unit_price_usd=100.0, kit_price_usd=None,
                      attrs={"extractor": "jsonld"}, **base)]
    anchor = [
        Listing(url="https://ex.com/p/1", title="P1 anchor",
                unit_price_usd=99.0, kit_price_usd=None,
                attrs={"extractor": "anchor_llm"}, **base),
        Listing(url="https://ex.com/p/2", title="P2 anchor",
                unit_price_usd=200.0, kit_price_usd=None,
                attrs={"extractor": "anchor_llm"}, **base),
    ]
    full_html = [
        Listing(url="https://ex.com/p/2", title="P2 full_html",
                unit_price_usd=201.0, kit_price_usd=None,
                attrs={"extractor": "full_html_llm"}, **base),
        Listing(url="https://ex.com/p/3", title="P3 full_html",
                unit_price_usd=300.0, kit_price_usd=None,
                attrs={"extractor": "full_html_llm"}, **base),
    ]
    merged = universal_ai._union_by_canonical(jsonld, anchor, full_html)
    assert len(merged) == 3
    by_url = {listing.url: listing for listing in merged}
    # P1: JSON-LD wins over anchor (first-seen)
    assert by_url["https://ex.com/p/1"].attrs["extractor"] == "jsonld"
    assert by_url["https://ex.com/p/1"].unit_price_usd == 100.0
    # P2: anchor wins over full_html (first-seen)
    assert by_url["https://ex.com/p/2"].attrs["extractor"] == "anchor_llm"
    # P3: only full_html contributed it — additive recall
    assert by_url["https://ex.com/p/3"].attrs["extractor"] == "full_html_llm"


def test_extract_via_full_html_recall_gap_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """The full-HTML extractor must find ALL 7 products in the recall-gap
    fixture (including the 5 whose prices aren't near their anchors).
    The anchor walker only finds 2 with prices — this proves the recall win."""
    from datetime import UTC, datetime

    html = RECALL_GAP_FIXTURE.read_text(encoding="utf-8")

    # The anchor walker finds 7 candidates but only 2 with price hints.
    cands = universal_ai._extract_candidates(html, base_url=RECALL_GAP_URL)
    priced = [c for c in cands if c["price_hints"]]
    assert len(priced) == 2, f"pre-condition: walker must find exactly 2 priced, got {len(priced)}"

    # Mock LLM to return all 7 products with their prices and link indices.
    anchors = universal_ai._collect_search_anchors(html, base_url=RECALL_GAP_URL)
    product_anchors = {a["title"]: a["idx"] for a in anchors if "/products/" in a["href_abs"]}

    def _llm(**kwargs: object) -> LLMResponse:
        import json as _json
        products = [
            {"title": "Bose QuietComfort 45 Wireless Headphones", "price_usd": 329.00,
             "link_idx": product_anchors.get("Bose QuietComfort 45 Wireless Headphones", 0)},
            {"title": "Bose Noise Cancelling Headphones 700", "price_usd": 379.00,
             "link_idx": product_anchors.get("Bose Noise Cancelling Headphones 700", 1)},
            {"title": "Bose Sport Earbuds", "price_usd": 149.00,
             "link_idx": product_anchors.get("Bose Sport Earbuds", 2)},
            {"title": "Bose QuietComfort Earbuds II", "price_usd": 279.00,
             "link_idx": product_anchors.get("Bose QuietComfort Earbuds II", 3)},
            {"title": "Bose SoundSport Free Wireless Earbuds", "price_usd": 199.00,
             "link_idx": product_anchors.get("Bose SoundSport Free Wireless Earbuds", 4)},
            {"title": "Bose Ultra Open Earbuds", "price_usd": 299.00,
             "link_idx": product_anchors.get("Bose Ultra Open Earbuds", 5)},
            {"title": "Bose QuietComfort Ultra Headphones", "price_usd": 429.00,
             "link_idx": product_anchors.get("Bose QuietComfort Ultra Headphones", 6)},
        ]
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text=_json.dumps({"products": products}),
            input_tokens=500, output_tokens=300,
        )

    monkeypatch.setattr(universal_ai, "call_llm", _llm)

    listings = universal_ai._extract_via_full_html(
        html, RECALL_GAP_URL,
        profile=None, fetched_at=datetime.now(tz=UTC),
        parsed_host="syntheticvendor.example.com",
    )
    assert len(listings) >= 7, (
        f"full-HTML extractor must find ≥7 products (recall-gap fixture); got {len(listings)}"
    )
    # All prices must pass the verbatim guard.
    for listing in listings:
        assert listing.unit_price_usd > 0
    # All extractor tags must be full_html_llm.
    assert all(listing.attrs.get("extractor") == "full_html_llm" for listing in listings)


def test_full_html_drops_fabricated_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """The anti-fabrication guard must drop products whose price doesn't
    appear verbatim in the fetched text (ADR-001)."""
    from datetime import UTC, datetime

    html = RECALL_GAP_FIXTURE.read_text(encoding="utf-8")
    anchors = universal_ai._collect_search_anchors(html, base_url=RECALL_GAP_URL)
    product_idx = next(
        a["idx"] for a in anchors if "bose-qc45" in a["href_abs"]
    )

    def _llm(**kwargs: object) -> LLMResponse:
        import json as _json
        # Return a fabricated price (999.99 does NOT appear in the fixture).
        return LLMResponse(
            provider="anthropic", model="claude-haiku-4-5",
            text=_json.dumps({"products": [
                {"title": "Bose QuietComfort 45", "price_usd": 999.99,
                 "link_idx": product_idx},
            ]}),
            input_tokens=200, output_tokens=50,
        )

    monkeypatch.setattr(universal_ai, "call_llm", _llm)

    listings = universal_ai._extract_via_full_html(
        html, RECALL_GAP_URL,
        profile=None, fetched_at=datetime.now(tz=UTC),
        parsed_host="syntheticvendor.example.com",
    )
    assert len(listings) == 0, (
        "fabricated price 999.99 must be dropped by the verbatim guard"
    )


def test_search_union_recall_gap_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the search union on the recall-gap fixture must emit
    MORE listings than the anchor walker alone. This is the core ADR-077
    regression guard."""
    html = RECALL_GAP_FIXTURE.read_text(encoding="utf-8")

    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0, **_kw: (html, 200, "stub"),
    )

    call_n = {"n": 0}
    anchors = universal_ai._collect_search_anchors(html, base_url=RECALL_GAP_URL)
    product_anchors = {a["title"]: a["idx"] for a in anchors if "/products/" in a["href_abs"]}

    def _llm(**kwargs: object) -> LLMResponse:
        import json as _json
        call_n["n"] += 1
        system = kwargs.get("system", "")

        if "enumerating EVERY product" in system:
            # Full-HTML tier: return all 7 products.
            products = [
                {"title": "Bose QuietComfort 45 Wireless Headphones", "price_usd": 329.00,
                 "link_idx": product_anchors.get("Bose QuietComfort 45 Wireless Headphones", 0)},
                {"title": "Bose Noise Cancelling Headphones 700", "price_usd": 379.00,
                 "link_idx": product_anchors.get("Bose Noise Cancelling Headphones 700", 1)},
                {"title": "Bose Sport Earbuds", "price_usd": 149.00,
                 "link_idx": product_anchors.get("Bose Sport Earbuds", 2)},
                {"title": "Bose QuietComfort Earbuds II", "price_usd": 279.00,
                 "link_idx": product_anchors.get("Bose QuietComfort Earbuds II", 3)},
                {"title": "Bose SoundSport Free Wireless Earbuds", "price_usd": 199.00,
                 "link_idx": product_anchors.get("Bose SoundSport Free Wireless Earbuds", 4)},
                {"title": "Bose Ultra Open Earbuds", "price_usd": 299.00,
                 "link_idx": product_anchors.get("Bose Ultra Open Earbuds", 5)},
                {"title": "Bose QuietComfort Ultra Headphones", "price_usd": 429.00,
                 "link_idx": product_anchors.get("Bose QuietComfort Ultra Headphones", 6)},
            ]
            return LLMResponse(
                provider="anthropic", model="claude-haiku-4-5",
                text=_json.dumps({"products": products}),
                input_tokens=500, output_tokens=300,
            )
        else:
            # Anchor-walker tier: only picks the 2 candidates with price hints.
            payload = _json.loads(kwargs["messages"][0].content)  # type: ignore[index]
            decisions = []
            for c in payload:
                if c["price_hints"]:
                    price = float(c["price_hints"][0].lstrip("$").replace(",", ""))
                    decisions.append({
                        "idx": c["idx"], "title": c["anchor_text"],
                        "price_usd": price, "condition": "new",
                    })
            return LLMResponse(
                provider="anthropic", model="claude-haiku-4-5",
                text=_json.dumps({"listings": decisions}),
                input_tokens=300, output_tokens=100,
            )

    monkeypatch.setattr(universal_ai, "call_llm", _llm)

    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": RECALL_GAP_URL},
    )
    results = universal_ai.fetch(query)

    # The anchor walker alone would yield 2 (only the priced candidates).
    # The union must yield >=7 (all products, deduplicated by canonical URL).
    assert len(results) >= 5, (
        f"search union must beat anchor-walker's 2; got {len(results)} "
        f"(expected ≥5 from the full-HTML tier's additive recall)"
    )
    # At least some results should be from the full-HTML extractor.
    full_html_count = sum(
        1 for r in results if r.attrs.get("extractor") == "full_html_llm"
    )
    assert full_html_count >= 3, (
        f"full-HTML tier must contribute ≥3 unique products; got {full_html_count}"
    )
    # The anchor-walker results are also preserved.
    anchor_count = sum(
        1 for r in results if r.attrs.get("extractor") == "anchor_llm"
    )
    assert anchor_count >= 2, (
        f"anchor-walker must contribute its 2 priced products; got {anchor_count}"
    )
    # LAST_RUN_USAGE should be accumulated across both LLM calls.
    assert universal_ai.LAST_RUN_USAGE is not None
    assert universal_ai.LAST_RUN_USAGE["input_tokens"] > 0


def test_accumulate_usage_sums_tokens() -> None:
    """_accumulate_usage must SUM tokens across multiple calls, not clobber."""
    universal_ai.LAST_RUN_USAGE = None

    class _FakeResp:
        input_tokens: int
        output_tokens: int

    r1 = _FakeResp()
    r1.input_tokens = 100
    r1.output_tokens = 50
    universal_ai._accumulate_usage(r1)
    assert universal_ai.LAST_RUN_USAGE["input_tokens"] == 100
    assert universal_ai.LAST_RUN_USAGE["output_tokens"] == 50

    r2 = _FakeResp()
    r2.input_tokens = 200
    r2.output_tokens = 75
    universal_ai._accumulate_usage(r2)
    assert universal_ai.LAST_RUN_USAGE["input_tokens"] == 300
    assert universal_ai.LAST_RUN_USAGE["output_tokens"] == 125


def test_extract_target_search_full_html_recall() -> None:
    """The committed Target search fixture must yield product-link anchors
    and prices in the stripped text — verifying the full-HTML tier has the
    raw material to improve recall on Target search pages."""
    html = TARGET_FIXTURE.read_text(encoding="utf-8")

    # The anchor walker finds ~46 candidates with prices.
    cands = universal_ai._extract_candidates(html, base_url=TARGET_URL)
    priced = [c for c in cands if c["price_hints"]]
    assert len(priced) >= 20, (
        f"pre-condition: Target walker must find many priced candidates, got {len(priced)}"
    )

    # The full-HTML search tier has access to the full stripped text.
    full_text = universal_ai._strip_to_main_text(html, max_chars=None)
    assert len(full_text) > 2000, "Target search page stripped text too short"

    # Search anchors include ALL links (not just product-filtered ones).
    anchors = universal_ai._collect_search_anchors(html, base_url=TARGET_URL)
    product_anchors = [a for a in anchors if "/p/" in a["href_abs"]]
    assert len(product_anchors) >= 20, (
        f"expected many /p/ product anchors for full-HTML tier, got {len(product_anchors)}"
    )

    # Prices from the stripped text must be verifiable.
    for test_price in [149.99, 179.99, 249.99]:
        assert universal_ai._price_in_text(test_price, full_text), (
            f"Target stripped text must contain {test_price} for verbatim guard"
        )


# --- Search-term Keyword Degradation Fallback (ADR-078) -------------------


def test_degrade_search_url() -> None:
    """_degrade_search_url should correctly drop the last word of a search query in different URL shapes."""
    # Query parameters based shapes
    assert universal_ai._degrade_search_url("https://www.walmart.com/search?q=dyson+v15+detect") == "https://www.walmart.com/search?q=dyson+v15"
    assert universal_ai._degrade_search_url("https://www.bestbuy.com/site/searchpage.jsp?st=dyson+v15") == "https://www.bestbuy.com/site/searchpage.jsp?st=dyson"
    assert universal_ai._degrade_search_url("https://www.williams-sonoma.com/search/results.html?keywords=dyson+v15+detect") == "https://www.williams-sonoma.com/search/results.html?keywords=dyson+v15"

    # Path based shapes
    assert universal_ai._degrade_search_url("https://www.target.com/s/dyson+v15+detect") == "https://www.target.com/s/dyson+v15"
    assert universal_ai._degrade_search_url("https://www.example.com/search/dyson+v15+detect") == "https://www.example.com/search/dyson+v15"

    # No degradation possible (1 word)
    assert universal_ai._degrade_search_url("https://www.target.com/s/dyson") is None
    assert universal_ai._degrade_search_url("https://www.bestbuy.com/site/searchpage.jsp?st=dyson") is None

    # No query parameters/search path at all
    assert universal_ai._degrade_search_url("https://www.example.com/about-us") is None


def test_fetch_search_degradation_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a primary search returns 0 listings, it should degrade the query and retry."""
    # We mock fetch to return 0 listings for the primary search (dyson+v15+detect),
    # but return 1 listing for the degraded search (dyson+v15).
    primary_url = "https://www.walmart.com/search?q=dyson+v15+detect"
    degraded_url = "https://www.walmart.com/search?q=dyson+v15"

    fetched_urls = []

    def _mock_fetch(url: str, timeout: float = 20.0, alterlab_options: dict[str, Any] | None = None) -> tuple[str, int, str]:
        fetched_urls.append(url)
        if url == primary_url:
            # Return empty page
            return "<html><body>No products found</body></html>", 200, "alterlab"
        elif url == degraded_url:
            # Return page with JSON-LD listing
            jsonld_html = """
            <script type="application/ld+json">
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Dyson V15 Detect Vacuum",
                "url": "https://www.walmart.com/ip/dyson-v15/123",
                "offers": {
                    "@type": "Offer",
                    "price": "599.99",
                    "priceCurrency": "USD",
                    "itemCondition": "https://schema.org/NewCondition"
                }
            }
            </script>
            """
            return jsonld_html, 200, "alterlab"
        return "", 404, "httpx"

    monkeypatch.setattr(universal_ai, "_fetch_html", _mock_fetch)
    monkeypatch.setenv("ALTERLAB_API_KEY", "dummy-key")

    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": primary_url}
    )

    listings = universal_ai.fetch(query)

    # We should have attempted to fetch both URLs in sequence
    assert primary_url in fetched_urls
    assert degraded_url in fetched_urls

    # The listing from the degraded URL should be successfully recovered
    assert len(listings) == 1
    assert listings[0].title == "Dyson V15 Detect Vacuum"
    assert listings[0].unit_price_usd == 599.99


# ---------------------------------------------------------------------------
# Phase 24 / ADR-082: Amazon JS-render extraction regression guard
# ---------------------------------------------------------------------------


def test_amazon_search_fixture_extracts_dp_candidates_with_prices() -> None:
    """ADR-082 regression guard: Amazon search HTML captured via the runtime
    path (``country: us, min_tier: 3, wait_condition: networkidle`` — the new
    ``default_alterlab_options`` for amazon.com) must yield product-shaped
    anchors with price hints.

    Phase 23 Part A (2026-05-24, commit a1f98dc) onboarded a profile WITHOUT
    these defaults and got ``fetched 0 / passed 0`` on every Amazon source
    because the static HTML had 1+ MB of body but 0 product-shaped anchors —
    JS-rendered tiles aren't present in the bare HTML. This fixture is the
    AlterLab+networkidle response, frozen forever, so a regression that
    blanks Amazon recall again will fail this test at import time.
    """
    fixture = FIXTURE_DIR / "amazon_search_logitech_mx_master_3s.html"
    html = fixture.read_text(encoding="utf-8")
    cands = universal_ai._extract_candidates(
        html, base_url="https://www.amazon.com/s?k=logitech+mx+master+3s"
    )

    # Product-shaped Amazon anchors live at /dp/<ASIN>. Filter to those and
    # require ≥1 price hint per the brief.
    dp_with_price = [
        c for c in cands if "/dp/" in c["href"] and c["price_hints"]
    ]
    assert len(dp_with_price) >= 5, (
        f"Expected >=5 /dp/ candidates with price hints; got "
        f"{len(dp_with_price)} out of {len(cands)} total candidates. "
        "If Amazon's tile markup changed, regenerate the fixture via "
        "`cli probe-url ... --save-body` AFTER confirming the live AlterLab "
        "tier-3 networkidle path returns product tiles."
    )

    # The target product the fixture was captured for must be in the set
    # (cheapest MX Master 3S Standard Edition at $89.99 in the captured run).
    mx_master_3s = [
        c for c in dp_with_price
        if "MX Master 3S" in c["anchor_text"]
    ]
    assert mx_master_3s, (
        "MX Master 3S itself not present in extracted candidates — "
        "recall regression on the target product the fixture was captured for."
    )

