"""Universal AI adapter for arbitrary vendor URLs.

Pipeline:
  1. Fetch the page. Prefer ``curl_cffi`` (real Chrome TLS fingerprint)
     so basic Cloudflare / TLS-fingerprint blocks don't zero a run; fall
     back to ``httpx`` when ``curl_cffi`` is not installed.
  2. Walk every ``<a href>`` in the DOM with selectolax. Build a candidate
     list of ``{idx, anchor_text, href_abs, price_hints, context}`` where
     ``href_abs`` is the URL exactly as it appears in the HTML (after
     ``urljoin`` normalisation), and ``price_hints`` are ``$X.XX``-style
     tokens from the nearest "card-like" ancestor's text.
  3. Hand the candidate list to Claude Haiku 4.5 with a tiny "pick real
     product listings, return clean title/price/condition keyed by idx"
     prompt. The LLM picks indices from a fixed set; URLs are never
     re-typed by the LLM, so URL hallucination is structurally impossible.
  4. Map the LLM verdicts back to the original candidates and emit
     ``Listing`` rows.

Anti-bot story today: TLS impersonation via ``curl_cffi`` is enough for
most server-rendered storefronts (Shopify, BigCommerce, basic
WooCommerce, many brand sites). Sites that gate on JS execution
(full Cloudflare challenge, Akamai, Datadome) still need a rendered
fetch — that path is intentionally deferred until a profile actually
requires it; the adapter logs the response status / body length so the
diagnostic surfaces in the worker log when this happens.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from product_search.llm import Message, call_llm
from product_search.models import AdapterQuery, Listing

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are extracting product listings from a vendor page.

The user will give you a JSON array of anchor candidates collected from a
single product search / category / collection page on an arbitrary vendor's
website. Each candidate has:
  - "idx": integer (the index you must echo back)
  - "anchor_text": the text inside the <a> tag (often the product title)
  - "price_hints": list of $X.XX-style strings found near this anchor
  - "context": short text snippet from the surrounding card

Your job: pick ONLY the candidates that are real product listings for sale
on this page (not navigation, not categories, not author bios, not "view
all" links, not cart/checkout, not breadcrumbs).

For each kept candidate, return:
  - "idx": integer (must match the input)
  - "title": cleaned title (use anchor_text or context, trim noise)
  - "price_usd": numeric only (e.g. 47.99). Pick the most plausible price
    from price_hints + context. If you cannot identify a price, OMIT this
    candidate entirely.
  - "condition": one of "new", "used", "refurbished" (default "new")

Output a JSON object: {"listings": [...]}

Rules:
  - Do NOT invent any field. Use only data present in the candidate.
  - Do NOT invent URLs (you don't even output URLs — they map by idx).
  - If a candidate has no price_hints and no $-amount in context, OMIT it.
  - If the same product appears multiple times (variants, etc.), keep ONE
    per distinct title.
  - Return an empty list if nothing on the page is a product listing.
  - Output JSON ONLY. No prose preamble, no markdown fences.
"""


# --- Module-level capture for cli.py's run-cost panel -----------------------

LAST_RUN_USAGE: dict[str, Any] | None = None


# --- HTTP fetch with TLS impersonation -------------------------------------


def _fetch_html(url: str, timeout: float = 20.0) -> tuple[str, int, str]:
    """Fetch ``url`` and return ``(html, status_code, fetcher_label)``.

    Three-tier fetch strategy:

    1. **ScrapFly** — when ``SCRAPFLY_API_KEY`` is set in the environment.
       Routes through their API with ``render_js=true`` (full headless-Chrome
       render) and ``asp=true`` (anti-scraping protection: residential
       proxies + challenge solving). Costs credits, but gets us past
       Cloudflare/Datadome/Akamai/full-React-SPA pages that the lower
       tiers can't touch. Free tier is ~1k credits/month.

    2. **curl_cffi** — Chrome TLS fingerprint impersonation. Free, fast,
       beats basic Cloudflare TLS-fingerprint blocks but does no JS
       execution. Works on most server-rendered storefronts.

    3. **httpx** — plain HTTP fallback when ``curl_cffi`` isn't installed.
       Default Python TLS fingerprint, fails on most modern bot detection.

    Either way the response body is returned verbatim — non-2xx status
    codes are logged but the body is still returned because some sites
    serve a challenge page with status 200 and others 403.
    """
    scrapfly_key = os.environ.get("SCRAPFLY_API_KEY", "").strip()
    if scrapfly_key:
        try:
            return _fetch_via_scrapfly(url, scrapfly_key, timeout=timeout)
        except Exception as exc:
            # Don't let a ScrapFly outage zero a run — fall through to
            # the cheap tiers. The worker log captures the failure so
            # repeated outages are debuggable.
            logger.warning(
                f"[universal_ai] ScrapFly fetch failed ({type(exc).__name__}: "
                f"{exc}); falling back to curl_cffi/httpx."
            )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        from curl_cffi import requests as cc_requests

        # ``impersonate="chrome"`` selects whatever the latest pinned
        # Chrome profile is (TLS, JA3, HTTP/2 settings — the full
        # fingerprint). Most server-rendered storefronts accept this.
        resp = cc_requests.get(
            url,
            headers=headers,
            timeout=timeout,
            impersonate="chrome",
            allow_redirects=True,
        )
        return resp.text or "", int(resp.status_code), "curl_cffi"
    except ImportError:
        pass

    import httpx

    with httpx.Client(follow_redirects=True, headers=headers, timeout=timeout) as client:
        resp = client.get(url)
        return resp.text or "", resp.status_code, "httpx"


def _fetch_via_scrapfly(
    url: str, api_key: str, *, timeout: float = 60.0
) -> tuple[str, int, str]:
    """Fetch ``url`` via the ScrapFly API with JS rendering + ASP.

    Returns ``(html, vendor_status_code, "scrapfly")``. The HTTP status
    we return is the ORIGIN site's status (e.g. 200 for the vendor),
    NOT ScrapFly's API status — that's what the rest of the adapter
    expects. ScrapFly itself either returns 200 with a JSON envelope
    or a non-2xx with an error JSON; both cases raise so the caller's
    try/except routes to the fallback tiers.

    JS rendering can take up to ~20s on heavy pages, so we use a
    longer default timeout than the cheap fetchers.
    """
    import httpx

    params = {
        "key": api_key,
        "url": url,
        "render_js": "true",
        "asp": "true",
        "country": "us",
    }
    api = "https://api.scrapfly.io/scrape"
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(api, params=params)
    resp.raise_for_status()
    payload = resp.json()
    result = payload.get("result") or {}
    html = result.get("content") or ""
    origin_status = int(result.get("status_code") or 0)
    return html, origin_status, "scrapfly"


# --- Candidate extraction --------------------------------------------------


# Skip anchors that obviously aren't product links.
_SKIP_HREF_PREFIXES = (
    "#",
    "javascript:",
    "mailto:",
    "tel:",
    "data:",
)

# Anchors whose text looks like UI chrome rather than a product title.
_UI_CHROME_TEXTS = {
    "add to cart", "add to bag", "add to wishlist", "quick view", "compare",
    "sign in", "login", "log in", "register", "create account", "menu",
    "search", "view cart", "checkout", "next", "previous", "more", "less",
    "view all", "see all", "shop all", "home", "back", "close", "skip",
    "filter", "sort", "share", "print", "save", "wishlist", "account",
}

_PRICE_PATTERN = re.compile(
    r"(?:US\s*\$|USD\s*\$|\$|\bUSD\s+)\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)",
    re.IGNORECASE,
)


def _looks_like_product_url(href: str) -> bool:
    """Heuristic: does ``href`` look like a product detail page URL?"""
    h = href.lower()
    # Common product-page path signals across many e-commerce platforms.
    signals = (
        "/product/", "/products/", "/p/", "/dp/", "/item/", "/itm/",
        "/listing/", "/buy/", "/sku/", "/pd/", "/shop/",
    )
    if any(s in h for s in signals):
        return True
    # Many storefronts encode the SKU in the path's last segment with
    # hyphens — accept those as a soft signal when other things don't disqualify.
    path = urlparse(h).path
    last = path.rsplit("/", 1)[-1]
    return bool(last) and "-" in last and len(last) >= 6


def _is_search_or_category_url(href: str) -> bool:
    """Disqualify obvious search-results / category / collection URLs."""
    h = href.lower()
    return any(s in h for s in (
        "/search", "?q=", "?query=", "?_nkw=", "/sch/", "/category/",
        "/categories/", "/collections/", "/c/", "/browse/",
    ))


def _ancestor_card_text(node: Any, max_hops: int = 4) -> str:
    """Walk up to ``max_hops`` parents and return the card-like ancestor's text.

    We stop early when we've climbed into a container so wide it's clearly
    no longer "this product card" — heuristically, when the ancestor's
    plain text exceeds ~600 chars (a card is typically smaller than a page).
    """
    cur = node
    last_text = node.text(separator=" ", strip=True) if hasattr(node, "text") else ""
    for _ in range(max_hops):
        parent = getattr(cur, "parent", None)
        if parent is None:
            break
        text = parent.text(separator=" ", strip=True)
        if len(text) > 600:
            return last_text
        last_text = text
        cur = parent
    return last_text


def _extract_candidates(
    html: str, base_url: str, *, max_candidates: int = 80
) -> list[dict[str, Any]]:
    """Extract anchor-based product candidates from raw HTML.

    Each candidate has ``{idx, anchor_text, href, price_hints, context}``
    with ``href`` resolved to an absolute URL via ``urljoin``. The idx
    field is a stable integer the LLM echoes back — no URL ever round-trips
    through the LLM.
    """
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        logger.error("selectolax is required for universal_ai extraction.")
        return []

    tree = HTMLParser(html)
    if tree.body is None:
        return []

    seen_canonical: set[str] = set()
    candidates: list[dict[str, Any]] = []

    for a in tree.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if not href:
            continue
        if href.startswith(_SKIP_HREF_PREFIXES):
            continue

        href_abs = urljoin(base_url, href)
        parsed = urlparse(href_abs)
        if parsed.scheme not in ("http", "https"):
            continue

        if _is_search_or_category_url(href_abs):
            continue

        # Dedupe on scheme+host+path (drop query/fragment) so the same
        # product linked from multiple cards collapses to one candidate.
        canonical = f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
        if canonical in seen_canonical:
            continue

        anchor_text = (a.text(separator=" ", strip=True) or "").strip()
        if anchor_text.lower() in _UI_CHROME_TEXTS:
            continue

        context = _ancestor_card_text(a)

        # An anchor needs SOME signal that it's a product:
        # either the URL itself looks product-like, or there's a price nearby.
        has_price = bool(_PRICE_PATTERN.search(context))
        if not (_looks_like_product_url(href_abs) or has_price):
            continue

        seen_canonical.add(canonical)

        price_hints = _PRICE_PATTERN.findall(context)
        # Trim context aggressively for token economy; the LLM doesn't
        # need the whole card, just enough to read title + price.
        if len(context) > 400:
            context = context[:400] + "…"

        candidates.append({
            "idx": len(candidates),
            "anchor_text": anchor_text[:240],
            "href": href_abs,
            "price_hints": [f"${p}" for p in price_hints[:5]],
            "context": context,
        })

        if len(candidates) >= max_candidates:
            break

    return candidates


# --- Main entry point ------------------------------------------------------


def fetch(query: AdapterQuery) -> list[Listing]:
    """Fetch and extract product listings from an arbitrary vendor URL."""
    global LAST_RUN_USAGE
    LAST_RUN_USAGE = None

    if os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes"):
        logger.info("WORKER_USE_FIXTURES=1; universal_ai returning empty list.")
        return []

    url = query.extra.get("url") or query.storefront_url
    if not url:
        logger.warning("No 'url' in profile source for universal_ai_search.")
        return []

    logger.info(f"[universal_ai] Fetching {url}")
    try:
        html, status, fetcher = _fetch_html(url)
    except Exception as exc:
        logger.error(f"[universal_ai] Fetch failed: {type(exc).__name__}: {exc}")
        return []

    logger.info(
        f"[universal_ai] Fetched via {fetcher}: status={status}, "
        f"body_len={len(html)} chars"
    )
    if not html:
        logger.warning(f"[universal_ai] Empty body for {url}.")
        return []

    candidates = _extract_candidates(html, base_url=url)
    if not candidates:
        logger.warning(
            f"[universal_ai] No product-anchor candidates extracted from {url}. "
            f"This usually means the site needs JS rendering (Cloudflare / Datadome / "
            f"client-side React) — TLS impersonation alone isn't enough."
        )
        return []

    logger.info(f"[universal_ai] {len(candidates)} candidate anchors extracted.")

    # Build the LLM payload — anchor index, text, price hints, context.
    # We deliberately omit `href` from the payload to save tokens; the LLM
    # only needs to identify by `idx` and we map back to candidates[idx].
    llm_payload = [
        {
            "idx": c["idx"],
            "anchor_text": c["anchor_text"],
            "price_hints": c["price_hints"],
            "context": c["context"],
        }
        for c in candidates
    ]

    try:
        resp = call_llm(
            provider="anthropic",
            model="claude-haiku-4-5",
            system=SYSTEM_PROMPT,
            messages=[Message(role="user", content=json.dumps(llm_payload, indent=2))],
            response_format="json",
            max_tokens=4096,
        )
    except Exception as exc:
        logger.error(f"[universal_ai] LLM call failed: {type(exc).__name__}: {exc}")
        return []

    LAST_RUN_USAGE = {
        "step": "universal_ai_search",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
    }

    parsed = _extract_json(resp.text or "")
    if parsed is None:
        logger.error(
            f"[universal_ai] JSON parse failed. Raw response (first 500 chars):\n"
            f"{(resp.text or '')[:500]}"
        )
        return []

    if isinstance(parsed, dict) and isinstance(parsed.get("listings"), list):
        verdicts = parsed["listings"]
    elif isinstance(parsed, list):
        verdicts = parsed
    else:
        logger.error(f"[universal_ai] Unexpected JSON shape: {str(parsed)[:300]}")
        return []

    fetched_at = datetime.now(tz=UTC)
    parsed_host = urlparse(url).netloc.lower()
    results: list[Listing] = []

    for v in verdicts:
        if not isinstance(v, dict):
            continue
        try:
            idx = int(v["idx"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= idx < len(candidates)):
            continue

        cand = candidates[idx]

        raw_price = v.get("price_usd")
        if raw_price is None:
            continue
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        title = (v.get("title") or cand["anchor_text"] or "").strip()
        if not title:
            continue

        condition = str(v.get("condition") or "new").strip().lower()
        if condition not in ("new", "used", "refurbished"):
            condition = "new"

        results.append(Listing(
            source="universal_ai_search",
            url=cand["href"],
            title=title[:300],
            fetched_at=fetched_at,
            brand=None,
            mpn=None,
            attrs={"vendor_host": parsed_host},
            condition=condition,
            is_kit=False,
            kit_module_count=1,
            unit_price_usd=price,
            kit_price_usd=None,
            quantity_available=None,
            seller_name=parsed_host,
            seller_rating_pct=None,
            seller_feedback_count=None,
            ship_from_country=None,
        ))

    logger.info(f"[universal_ai] Emitted {len(results)} listings from {url}.")
    return results


# --- Local copy of the prose-tolerant JSON parser (mirrors ai_filter) -------


def _extract_json(text: str) -> object | None:
    """Return the first valid JSON value embedded in ``text``, else None.

    Mirrors :func:`product_search.validators.ai_filter._extract_json` so
    universal_ai stays standalone (and so unit tests don't need to import
    the validators module). Walks from the first ``{`` or ``[`` and uses
    ``json.JSONDecoder.raw_decode`` to handle prose preambles cleanly.
    """
    text = text.strip()
    if text.startswith("```"):
        # Strip ```json fences when the model adds them despite json mode.
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3].strip()
    text = text.removeprefix("json").strip()

    try:
        parsed: object = json.loads(text)
        return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, _end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            continue
        return obj  # type: ignore[no-any-return]
    return None
