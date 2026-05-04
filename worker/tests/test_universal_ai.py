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


def test_alterlab_fetch_path_used_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ALTERLAB_API_KEY is set, _fetch_html routes through AlterLab
    (not curl_cffi/httpx) and returns the origin HTML from the JSON envelope."""
    monkeypatch.setenv("ALTERLAB_API_KEY", "test-key-12345")

    captured: dict[str, Any] = {}

    class _StubClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

        def __enter__(self) -> "_StubClient":
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


def test_fetch_uses_jsonld_and_skips_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """When JSON-LD yields listings, fetch() must NOT call the LLM —
    that's the whole point of the tier (zero-cost extraction)."""
    monkeypatch.setattr(
        universal_ai, "_fetch_html",
        lambda url, timeout=20.0: (
            SHOPIFY_JSONLD_FIXTURE.read_text(encoding="utf-8"), 200, "stub",
        ),
    )

    def _llm_should_not_be_called(**_: object) -> Any:  # pragma: no cover
        raise AssertionError("LLM must not run when JSON-LD already yielded listings")

    monkeypatch.setattr(universal_ai, "call_llm", _llm_should_not_be_called)

    query = AdapterQuery(
        source_id="universal_ai_search",
        extra={"url": SHOPIFY_BASE_URL},
    )
    results = universal_ai.fetch(query)

    assert len(results) == 2
    urls = {r.url for r in results}
    assert "https://shop.synthstore.example.com/products/qc700" in urls
    assert "https://shop.synthstore.example.com/products/qc700-refurb" in urls
    # The JSON-LD path skips the LLM entirely; LAST_RUN_USAGE stays None.
    assert universal_ai.LAST_RUN_USAGE is None
    # Diagnostic tag so downstream code can tell the tiers apart.
    assert all(r.attrs.get("extractor") == "jsonld" for r in results)


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
        lambda url, timeout=20.0: (html, 200, "stub"),
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
