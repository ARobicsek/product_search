"""Universal AI adapter for arbitrary vendor URLs.

Pipeline:
  1. Fetch the page. AlterLab (rendered) when ``ALTERLAB_API_KEY`` is set,
     else ``curl_cffi`` (real Chrome TLS fingerprint), else ``httpx``.
  2. **JSON-LD tier** (Phase 15). Walk every
     ``<script type="application/ld+json">`` block. If any contain
     ``Product`` / ``Offer`` / ``ItemList`` types, extract ``name``,
     ``offers.price``, ``url`` directly. Most modern e-commerce embeds
     this for SEO. **Zero LLM cost when it works** — we return early.
  3. Walk every ``<a href>`` in the DOM with selectolax. Build a candidate
     list of ``{idx, anchor_text, href_abs, price_hints, context}`` where
     ``href_abs`` is the URL exactly as it appears in the HTML (after
     ``urljoin`` normalisation), and ``price_hints`` are ``$X.XX``-style
     tokens from the nearest "card-like" ancestor's text.
  4. Hand the candidate list to Claude Haiku 4.5 with a tiny "pick real
     product listings, return clean title/price/condition keyed by idx"
     prompt. The LLM picks indices from a fixed set; URLs are never
     re-typed by the LLM, so URL hallucination is structurally impossible.
  5. Map the LLM verdicts back to the original candidates and emit
     ``Listing`` rows.

Anti-bot story today: TLS impersonation via ``curl_cffi`` is enough for
most server-rendered storefronts (Shopify, BigCommerce, basic
WooCommerce, many brand sites). Sites that gate on JS execution
(full Cloudflare challenge, Akamai, Datadome) need AlterLab.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
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
  - "pack_size": integer. If the title or context indicates a multi-pack, bundle, count,
    or kit (e.g. "2-pack", "5 pack", "6 count", "8x32GB"), extract the number of items/units sold. Default to 1.
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


# Tier 1.5 (Phase 19 / ADR-049): single-product detail-page extractor.
# Runs ONLY for sources flagged a product-detail page (page_type: "detail",
# or the URL-shape heuristic when page_type is absent) AND only after the
# JSON-LD tier found nothing. For single-SKU products (one exact part
# number) the vendors that stock it often expose it ONLY on JS-heavy detail
# pages with no JSON-LD and no clean product anchors — Tier 1 misses, Tier 2
# correctly rejects the nav junk and emits 0. This tier reads the stripped
# page text and asks for THE one product's price, then deterministically
# re-verifies that price string occurs verbatim in the fetched bytes before
# emitting (ADR-001 — stricter than the anchor tier).
DETAIL_SYSTEM_PROMPT = """You are extracting THE single product from one \
vendor product-detail page.

The user message is the visible text of ONE product's detail page
(navigation, header, scripts and footer already stripped). The page is for
exactly one product for sale.

Return a JSON object with EXACTLY these keys:
  - "found": true or false
  - "title": the product title as shown on the page
  - "price_usd": numeric only (e.g. 2335.00). The CURRENT selling price for
    THIS product.
  - "condition": one of "new", "used", "refurbished" (default "new")
  - "in_stock": true or false
  - "pack_size": integer — units sold in one purchase (default 1)

Hard rules:
  - The price MUST appear verbatim in the provided text. If you cannot find
    an unambiguous current selling price for THIS product, return
    {"found": false} (the other keys then do not matter).
  - Do NOT invent, estimate, round, or currency-convert any value. Copy the
    price digits exactly as they appear (ignore the currency symbol and any
    thousands separators).
  - Use the current buy price — NOT list/MSRP/strikethrough/"was" price, NOT
    a bundled accessory, financing installment, or "related products" price.
  - "condition" stays "new" unless the page explicitly states otherwise.
  - "in_stock" is false if the page says out of stock / sold out / backorder
    / "notify me" / "email when available"; true otherwise.
  - Output JSON ONLY. No prose preamble, no markdown fences.
"""


# --- Module-level capture for cli.py's run-cost panel -----------------------

LAST_RUN_USAGE: dict[str, Any] | None = None


# --- HTTP fetch with TLS impersonation -------------------------------------


def _fetch_html(
    url: str,
    timeout: float = 20.0,
    alterlab_options: dict[str, Any] | None = None,
) -> tuple[str, int, str]:
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
    alterlab_key = os.environ.get("ALTERLAB_API_KEY", "").strip()
    if alterlab_key:
        # AlterLab needs its own (much longer) timeout: render_js spins up
        # a real Chrome and can take 30-60s on heavy pages (B&H, Crutchfield).
        # The outer `timeout` arg is sized for the cheap raw-HTTP fetchers and
        # would prematurely abort an in-flight render.
        try:
            return _fetch_via_alterlab(
                url,
                alterlab_key,
                timeout=120.0,
                alterlab_options=alterlab_options,
            )
        except Exception as exc:
            # Check if quota/auth error and bubble it up instead of fallback
            if hasattr(exc, "response") and exc.response.status_code in (401, 403, 429):
                # We raise so fetch() catches it and bubbles up to cli.py
                raise RuntimeError(f"AlterLab API issue: HTTP {exc.response.status_code} quota or auth error") from exc
            # Don't let an AlterLab outage zero a run — fall through to
            # the cheap tiers. The worker log captures the failure so
            # repeated outages are debuggable.
            logger.warning(
                f"[universal_ai] AlterLab fetch failed ({type(exc).__name__}: "
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
        httpx_resp = client.get(url)
        return httpx_resp.text or "", httpx_resp.status_code, "httpx"


def _fetch_via_alterlab(
    url: str,
    api_key: str,
    *,
    timeout: float = 60.0,
    alterlab_options: dict[str, Any] | None = None,
) -> tuple[str, int, str]:
    """Fetch ``url`` via the AlterLab API with JS rendering.

    Returns ``(html, vendor_status_code, "alterlab")``. The HTTP status
    we return is the ORIGIN site's status (e.g. 200 for the vendor),
    NOT AlterLab's API status — that's what the rest of the adapter
    expects. AlterLab itself either returns 200 with a JSON envelope
    or a non-2xx with an error JSON; both cases raise so the caller's
    try/except routes to the fallback tiers or bubbles up auth/quota errors.

    Wire format (per https://alterlab.io/docs/api/rest):
      POST https://api.alterlab.io/api/v1/scrape
      Header: X-API-Key: <key>
      Body:   {"url": ..., "sync": true, "formats": ["html"],
               "advanced": {"render_js": true}}
      Resp:   {"status_code": <origin>, "content": {"html": "..."} | "..."}

    ``formats: ["html"]`` makes ``content`` deterministically an object
    with an ``html`` field (vs a bare string in some sync responses).
    """
    import httpx

    body = {
        "url": url,
        "sync": True,
        "formats": ["html"],
        "asp": True,
        "advanced": {"render_js": True},
    }
    if alterlab_options:
        for key in ["country", "min_tier", "wait_for", "render_js", "asp"]:
            if key in alterlab_options and alterlab_options[key] is not None:
                if key in ["wait_for", "render_js"]:
                    body["advanced"][key] = alterlab_options[key]
                else:
                    body[key] = alterlab_options[key]

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    api = "https://api.alterlab.io/api/v1/scrape"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(api, json=body, headers=headers)
    resp.raise_for_status()
    payload = resp.json()

    content = payload.get("content")
    if isinstance(content, dict):
        html = content.get("html") or ""
    elif isinstance(content, str):
        html = content
    else:
        html = ""
    origin_status = int(payload.get("status_code") or 0)
    return html, origin_status, "alterlab"


# --- Bounded retry for transient fetch failures (ADR-053) ------------------
#
# `_fetch_html` cascades AlterLab -> curl_cffi -> httpx, but the whole
# cascade was attempted exactly once. A single transient connection/read
# timeout therefore dropped the source for the entire run. On 2026-05-18 a
# 20 s curl(28) connection timeout knocked provantage.com out of the
# amd-epyc-9255 run; because provantage had been the cheapest listing the
# prior run ($2117 vs the $2795 that then became the headline), one flaky
# socket silently moved the report's bottom line. ADR-053: retry the whole
# fetch ONCE on timeout/connection-class errors only. Auth/quota errors
# (AlterLab 401/403/429) are deliberately NOT retried -- a retry cannot fix
# them and would re-spend the (up to 120 s) AlterLab budget for nothing.
# Accepted tradeoff: a genuinely-down host now costs ~2x the cascade once;
# that latency is the price of not losing a transient winner.
_FETCH_MAX_ATTEMPTS = 2
_FETCH_RETRY_BACKOFF_SECONDS = 2.0


def _is_retryable_fetch_error(exc: BaseException) -> bool:
    """True if ``exc`` is a transient timeout/connection failure worth one retry.

    Explicitly NOT retryable: AlterLab auth/quota, raised as a ``RuntimeError``
    tagged ``"AlterLab API issue"`` -- retrying cannot fix a 401/403/429 and
    would re-spend the long AlterLab timeout for nothing.
    """
    msg = str(exc)
    if isinstance(exc, RuntimeError) and "AlterLab API issue" in msg:
        return False

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)):
            return True
    except ImportError:
        pass

    # curl_cffi raises its own timeout/connection types; match the libcurl
    # error string when the class isn't importable. 28 = operation timed
    # out, 7 = couldn't connect, 6 = couldn't resolve host, 35 = SSL connect.
    lowered = msg.lower()
    return any(
        marker in lowered
        for marker in (
            "timed out",
            "timeout",
            "curl: (28)",
            "curl: (7)",
            "curl: (6)",
            "curl: (35)",
            "connection refused",
            "connection reset",
        )
    )


def _fetch_html_with_retry(
    url: str,
    alterlab_options: dict[str, Any] | None = None,
) -> tuple[str, int, str]:
    """``_fetch_html`` with one bounded retry on transient fetch errors.

    Non-retryable errors (auth/quota, parse, anything not timeout/connection
    class) propagate on the first attempt with no delay. See ADR-053.
    """
    for attempt in range(1, _FETCH_MAX_ATTEMPTS + 1):
        try:
            if alterlab_options:
                return _fetch_html(url, alterlab_options=alterlab_options)
            else:
                return _fetch_html(url)
        except Exception as exc:
            if attempt < _FETCH_MAX_ATTEMPTS and _is_retryable_fetch_error(exc):
                logger.warning(
                    f"[universal_ai] Fetch attempt {attempt}/{_FETCH_MAX_ATTEMPTS} "
                    f"for {url} hit a transient error "
                    f"({type(exc).__name__}: {exc}); retrying after "
                    f"{_FETCH_RETRY_BACKOFF_SECONDS:.0f}s."
                )
                time.sleep(_FETCH_RETRY_BACKOFF_SECONDS)
                continue
            raise
    # Unreachable: the loop either returns or raises on the final attempt.
    raise AssertionError("unreachable: _fetch_html_with_retry exhausted loop")


# --- JSON-LD / microdata extraction ----------------------------------------


_CONDITION_MAP = {
    "newcondition": "new",
    "usedcondition": "used",
    "refurbishedcondition": "refurbished",
    "damagedcondition": "used",
}


def _jsonld_blocks(html: str) -> list[Any]:
    """Return parsed JSON values from every ``<script type="application/ld+json">``.

    Skips blocks that fail to parse (vendors occasionally embed broken JSON
    with comments or trailing commas — we just ignore those).
    """
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        logger.error("selectolax is required for universal_ai extraction.")
        return []

    tree = HTMLParser(html)
    blocks: list[Any] = []
    for node in tree.css('script[type="application/ld+json"]'):
        raw = (node.text() or "").strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return blocks


def _walk_jsonld(node: Any) -> Any:
    """Yield every dict reachable from ``node`` (including ``@graph`` and
    ``itemListElement`` recursion). We don't filter by ``@type`` here —
    callers do that — because the same payload often nests Products inside
    ``ListItem``/``ItemList`` wrappers and we want to see them all."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk_jsonld(v)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item)


def _has_type(obj: dict[str, Any], type_name: str) -> bool:
    """``@type`` may be a string OR a list of strings (Schema.org allows both)."""
    t = obj.get("@type")
    if isinstance(t, str):
        return t == type_name
    if isinstance(t, list):
        return type_name in t
    return False


def _coerce_price(value: Any) -> float | None:
    """Coerce a JSON-LD price field to a positive float, else None.

    Real-world prices come in as ``"249.99"``, ``249.99``, ``"$249.99"``,
    ``"249,99"`` (European), or even ``"From $249"``. Be defensive.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if f > 0 else None
    if isinstance(value, str):
        # Strip everything except digits, comma, dot, minus.
        m = re.search(r"\d+(?:[.,]\d+)?", value)
        if not m:
            return None
        s = m.group(0)
        # Normalize European "1.234,56" → "1234.56" or "12,99" → "12.99":
        # if there's exactly one comma and no dot, treat comma as decimal.
        if "," in s and "." not in s:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
        try:
            f = float(s)
            return f if f > 0 else None
        except ValueError:
            return None
    return None


def _offer_price_and_condition(offers: Any) -> tuple[float | None, str]:
    """Extract (price, condition) from a JSON-LD ``offers`` value.

    ``offers`` can be a single Offer dict, a list of Offers, or an
    AggregateOffer with ``lowPrice`` / ``highPrice``. Returns the
    cheapest plausible price, defaulting condition to "new".
    """
    candidates: list[tuple[float, str]] = []

    def _condition_from(o: dict[str, Any]) -> str:
        raw = o.get("itemCondition") or ""
        if isinstance(raw, dict):
            raw = raw.get("@id") or raw.get("name") or ""
        if not isinstance(raw, str):
            return "new"
        # URL forms: "https://schema.org/NewCondition" → "newcondition"
        key = raw.rsplit("/", 1)[-1].lower()
        return _CONDITION_MAP.get(key, "new")

    def _consider(o: dict[str, Any]) -> None:
        if _has_type(o, "AggregateOffer"):
            p = _coerce_price(o.get("lowPrice")) or _coerce_price(o.get("price"))
            if p is not None:
                candidates.append((p, _condition_from(o)))
            return
        # Plain Offer or untyped offer-shaped dict.
        p = _coerce_price(o.get("price"))
        if p is not None:
            candidates.append((p, _condition_from(o)))

    if isinstance(offers, dict):
        _consider(offers)
    elif isinstance(offers, list):
        for o in offers:
            if isinstance(o, dict):
                _consider(o)

    if not candidates:
        return None, "new"
    candidates.sort(key=lambda t: t[0])
    return candidates[0]


_PACK_PATTERNS = re.compile(
    r"\b(\d+)\s*x\s*(\d+)\s*gb\b"
    r"|\b(\d+)(?:\s*[-–]|\s+)(?:pack|count)\b"
    r"|\bkit\s+of\s+(\d+)\b"
    r"|\b(\d+)\s*pcs?\b",
    re.IGNORECASE,
)


def _parse_pack(
    title: str, price_usd: float, llm_pack_size: int = 1
) -> tuple[bool, int, float, float | None]:
    """Return (is_kit, kit_module_count, unit_price_usd, kit_price_usd)."""
    count = llm_pack_size
    if count <= 1:
        m = _PACK_PATTERNS.search(title)
        if m:
            count_str = next(g for g in m.groups() if g is not None)
            try:
                count = int(count_str)
            except ValueError:
                count = 1
    if count > 1:
        return True, count, round(price_usd / count, 2), price_usd
    return False, 1, price_usd, None


def _extract_jsonld_listings(
    html: str, base_url: str
) -> list[dict[str, Any]]:
    """Extract product listings from JSON-LD blocks.

    Returns a list of ``{title, url, price_usd, condition}`` dicts —
    the same shape the downstream emitter expects. Empty list if no
    Product blocks found, or if found Products lack price+url.

    Handles three common patterns:
      * Single ``Product`` (a product detail page).
      * ``ItemList`` with ``itemListElement`` of ``ListItem`` → ``Product``
        (a category / collection page on Shopify, BigCommerce, Magento).
      * ``@graph`` array containing multiple ``Product`` objects (various
        custom stacks).

    A Product without resolvable URL+price is dropped — downstream code
    refuses to invent the missing field.
    """
    listings: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for block in _jsonld_blocks(html):
        for obj in _walk_jsonld(block):
            if not isinstance(obj, dict) or not _has_type(obj, "Product"):
                continue

            name = obj.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            title = name.strip()[:300]

            url_raw = obj.get("url")
            if not isinstance(url_raw, str) or not url_raw.strip():
                continue
            url_abs = urljoin(base_url, url_raw.strip())
            parsed = urlparse(url_abs)
            if parsed.scheme not in ("http", "https"):
                continue

            # Dedupe on scheme+host+path to match anchor-tier behaviour.
            canonical = (
                f"{parsed.scheme}://{parsed.netloc.lower()}"
                f"{parsed.path.rstrip('/')}"
            )
            if canonical in seen_urls:
                continue

            price, condition = _offer_price_and_condition(obj.get("offers"))
            if price is None:
                continue

            seen_urls.add(canonical)
            listings.append({
                "title": title,
                "url": url_abs,
                "price_usd": price,
                "condition": condition,
            })

    return listings


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
# Path-prefix filtering (``_looks_like_nav_path``) handles most nav links by
# URL — this list is the backstop for sites with weird URL shapes.
_UI_CHROME_TEXTS = {
    "add to cart", "add to bag", "add to wishlist", "quick view", "compare",
    "sign in", "login", "log in", "register", "create account", "menu",
    "search", "view cart", "checkout", "next", "previous", "more", "less",
    "view all", "see all", "shop all", "home", "back", "close", "skip",
    "filter", "sort", "share", "print", "save", "wishlist", "account",
    "contact us", "about us", "buying guides", "ranking lists",
    "find stores", "weekly ad", "registry & wish list", "track order",
}

# Path prefixes that almost always mean nav / CMS / chrome rather than a
# product detail page. Compared as exact match OR `<prefix>/...` so e.g.
# ``/about`` and ``/about/our-story`` both match, but ``/about-us-bose-x``
# (a hypothetical product slug) does not.
_NAV_PATHS = (
    "/about", "/contact", "/blog", "/blogs", "/news", "/press",
    "/pages", "/page", "/articles", "/article", "/help", "/support",
    "/policies", "/policy", "/guides", "/guide", "/legal", "/faq",
    "/learn", "/community", "/locations", "/store-locator", "/find-stores",
    "/stores", "/account", "/wishlist", "/cart", "/checkout", "/login",
    "/signin", "/sign-in", "/register", "/track-order", "/orders",
    "/careers", "/jobs", "/feedback", "/reviews",
)

_PRICE_PATTERN = re.compile(
    r"(?:US\s*\$|USD\s*\$|\$|\bUSD\s+)\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)",
    re.IGNORECASE,
)

# Phase 19b: strip non-USD currency amounts from card context text.
# AlterLab's outbound IPs geo-route to Europe, so Amazon shows EUR prices
# in the rendered DOM (e.g. ``EUR\u20ac490.07``, ``\u00a3329.99``). The generic
# ``_PRICE_PATTERN`` requires ``$`` so these don't produce price_hints, but
# they leak into the LLM context and the LLM reads the digits as a USD price.
# Stripping them from the context before it reaches the LLM is the cleanest
# fix — we can't control AlterLab's exit geography.
_FOREIGN_CURRENCY_RE = re.compile(
    r"(?:"
    r"EUR\s*\u20ac|"                 # EUR€
    r"\bEUR[\s\u00a0]+(?=\d)|"       # EUR followed by whitespace (AlterLab: EUR&nbsp;490)
    r"\u20ac|"                       # bare €
    r"GBP\s*\u00a3|"                 # GBP£
    r"\u00a3|"                       # bare £
    r"CA?\$|"                        # C$ or CA$ (Canadian)
    r"A\$|"                          # A$ (Australian)
    r"\u00a5|"                       # ¥ (Yen / Yuan)
    r"\u20b9|"                       # ₹ (Indian Rupee)
    r"\bCHF\s+"                      # Swiss Franc
    r")\s*\d{1,6}(?:[.,]\d{2,3})*",
    re.IGNORECASE,
)

# Amazon and similar SPAs render prices as
#   <span class="a-price-symbol">$</span>
#   <span class="a-price-whole">329</span>
#   <span class="a-price-fraction">99</span>
# When selectolax flattens that to text with `separator=" "`, we get
# ``$ 329 99`` — the standard ``_PRICE_PATTERN`` matches only ``$329``
# (capturing the integer half and stopping at the space), giving us a
# wrong price. Pre-canonicalise these split prices into ``$329.99`` BEFORE
# running the standard pattern so both the joined and split markup yield
# the same result.
_SPLIT_PRICE_RE = re.compile(
    # Allow ``.`` in the gap because Amazon emits a literal
    # <span class="a-price-decimal">.</span> between the whole and
    # fraction spans — selectolax surfaces that as " . " in the flattened
    # text, not just whitespace.
    r"\$\s+(\d{1,4}(?:,\d{3})*)[\s.]+(\d{2})\b",
)


def _strip_foreign_currencies(text: str) -> str:
    """Remove non-USD currency amounts so the LLM can't misinterpret them.

    AlterLab's European exit IPs cause Amazon (and other vendors) to embed
    EUR / GBP / etc. prices in the rendered HTML. These leak into the
    flattened card context and the LLM reads them as if they were USD,
    producing wrong prices (e.g. EUR€490.07 → $490.07).
    """
    return _FOREIGN_CURRENCY_RE.sub("", text)


# Phase 19b: exchange rates for converting foreign-currency prices to USD.
# Live rates fetched from the Frankfurter API (free, no key, ECB-backed),
# cached for the lifetime of the process.  Hardcoded fallbacks used only
# when the API is unreachable (e.g. no internet in a test runner).
_FX_FALLBACK: dict[str, float] = {
    "EUR": 1.08,
    "GBP": 1.27,
    "CAD": 0.73,
    "AUD": 0.65,
    "JPY": 0.0067,
    "INR": 0.012,
    "CHF": 1.13,
}

_fx_cache: dict[str, float] | None = None


def _get_fx_rates() -> dict[str, float]:
    """Return a ``{currency_code: rate_to_usd}`` dict.

    Fetches live rates from ``api.frankfurter.dev`` on first call and
    caches for the rest of the process.  Falls back to ``_FX_FALLBACK``
    if the network call fails.
    """
    global _fx_cache
    if _fx_cache is not None:
        return _fx_cache

    try:
        import httpx
        resp = httpx.get(
            "https://api.frankfurter.dev/v1/latest?base=USD",
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        # The API returns rates relative to USD=1, so we invert them:
        # if EUR rate is 0.926, then 1 EUR = 1/0.926 = 1.0799 USD.
        raw_rates = data.get("rates", {})
        _fx_cache = {}
        for code, rate_from_usd in raw_rates.items():
            if rate_from_usd and rate_from_usd > 0:
                _fx_cache[code] = round(1.0 / rate_from_usd, 6)
        logger.debug(
            f"[universal_ai] Fetched live FX rates for {len(_fx_cache)} currencies."
        )
        return _fx_cache
    except Exception as exc:
        logger.warning(
            f"[universal_ai] FX rate fetch failed ({type(exc).__name__}: {exc}); "
            f"using hardcoded fallback rates."
        )
        _fx_cache = dict(_FX_FALLBACK)
        return _fx_cache

_FOREIGN_PRICE_EXTRACT_RE = re.compile(
    r"(?P<cur>"
    r"EUR\s*\u20ac|"                   # EUR€
    r"\bEUR[\s\u00a0]+(?=\d)|"         # EUR + whitespace (AlterLab: EUR&nbsp;490)
    r"\u20ac|"                         # bare €
    r"GBP\s*\u00a3|\u00a3|"           # GBP / £
    r"CA?\$|"                         # C$ / CA$
    r"A\$|"                           # A$
    r"\u00a5|"                        # ¥
    r"\u20b9|"                        # ₹
    r"\bCHF\s+"                       # CHF
    r")\s*(?P<amt>\d{1,6}(?:[.,]\d{2,3})*)",
    re.IGNORECASE,
)


def _foreign_price_to_usd(raw_text: str) -> tuple[float, str] | None:
    """Extract the first foreign-currency amount from ``raw_text`` and
    return an approximate USD equivalent and the currency code, else None.

    Used when AlterLab's European exit IPs cause Amazon to show EUR/GBP
    prices instead of USD.  Better to report an approximate price than
    to drop the listing.
    """
    m = _FOREIGN_PRICE_EXTRACT_RE.search(raw_text)
    if not m:
        return None
    cur_token = m.group("cur").strip().upper()
    # Normalise the token to a 3-letter code.
    if "\u20ac" in cur_token or cur_token.startswith("EUR"):
        code = "EUR"
    elif "\u00a3" in cur_token or cur_token.startswith("GBP"):
        code = "GBP"
    elif cur_token in ("C$", "CA$"):
        code = "CAD"
    elif cur_token == "A$":
        code = "AUD"
    elif "\u00a5" in cur_token:
        code = "JPY"
    elif "\u20b9" in cur_token:
        code = "INR"
    elif cur_token.startswith("CHF"):
        code = "CHF"
    else:
        return None
    rate = _get_fx_rates().get(code)
    if rate is None:
        return None
    amt_str = m.group("amt")
    # Normalise European "490,07" → "490.07".
    if "," in amt_str and "." not in amt_str:
        amt_str = amt_str.replace(",", ".")
    else:
        amt_str = amt_str.replace(",", "")
    try:
        return round(float(amt_str) * rate, 2), code
    except ValueError:
        return None


def _canonicalize_prices(text: str) -> str:
    """Rewrite ``$ N NN`` split-price runs into ``$N.NN`` so the standard
    price regex sees a single contiguous token."""
    return _SPLIT_PRICE_RE.sub(r"$\1.\2", text)


def _looks_like_product_url(href: str) -> bool:
    """Heuristic: does ``href`` look like a product detail page URL?"""
    h = href.lower()
    # Common product-page path signals across many e-commerce platforms.
    signals = (
        "/product/", "/products/", "/p/", "/dp/", "/item/", "/itm/",
        "/listing/", "/buy/", "/sku/", "/pd/", "/shop/",
        "/w/",    # ThriftBooks work pages: /w/<title>/<id>/
        "/book/", # Biblio, BetterWorldBooks: /book/<slug>/<id>
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


def _looks_like_nav_path(href: str) -> bool:
    """Disqualify hrefs whose path is a CMS / chrome nav path.

    Phase 15: catches common Shopify ``/pages/contact-us``,
    ``/blogs/buying-guides``, big-box ``/store-locator``, etc., which the
    bare ``_looks_like_product_url`` heuristic was passing because their
    last path segment happens to be hyphenated and >=6 chars.
    """
    path = urlparse(href).path.lower().rstrip("/")
    if path in ("", "/"):
        return True  # home / root link
    for nav in _NAV_PATHS:
        if path == nav or path.startswith(nav + "/"):
            return True
    return False


def _anchor_title(node: Any) -> str:
    """Extract a usable title from an anchor.

    Prefer the anchor's own text. When that's empty (an anchor wrapping just
    an image, common on big-box stores), fall back in order:
      1. descendant ``<img alt="...">`` — the most common pattern
      2. ``aria-label`` on the anchor itself
      3. ``title`` attribute on the anchor itself
    """
    txt = (node.text(separator=" ", strip=True) or "").strip()
    if txt:
        return txt
    # selectolax doesn't bind ``css`` directly to nodes in older versions —
    # iter via traverse() and pick the first <img> with a non-empty alt.
    if hasattr(node, "css"):
        for img in node.css("img"):
            alt = (img.attributes.get("alt") or "").strip()
            if alt:
                return alt
    aria = (node.attributes.get("aria-label") or "").strip()
    if aria:
        return aria
    return (node.attributes.get("title") or "").strip()


def _amazon_card_primary_price(node: Any) -> str | None:
    """For Amazon search-result cards, return the buy-now price digits.

    Amazon emits multiple prices per card: the primary new-condition price,
    a strikethrough List/MSRP, often a "From: $X" used/marketplace price,
    sometimes a Subscribe-and-Save discount. The generic
    ``_PRICE_PATTERN`` sweep over the card's flattened text grabs ALL of
    them, then the LLM picks the cheapest — recording the wrong number.

    Phase 19 fix (Breville BES876BSS Impress recorded as $489.50 in the
    2026-05-04 run; actual page price $649.95): when the anchor sits inside
    an ``s-result-item`` card on amazon.<tld>, walk to the card boundary and
    pick the FIRST ``<span class="a-price">`` that is NOT a strikethrough
    list-price variant. Its ``<span class="a-offscreen">`` carries a
    fully-formed ``$NNN.NN`` accessibility string; that's the buy-now price.

    Returns the price digits (e.g. ``"649.95"``) without the leading ``$``.
    Returns None when no card boundary is found or no qualifying price
    exists in the card — in which case the caller falls back to the
    generic regex sweep.
    """
    # Walk up until we find the card boundary OR exhaust parents.  The
    # original cap of 10 hops was too tight: deeply-nested anchors (rating
    # widgets, nested icons) can sit 10+ levels under ``s-result-item``,
    # and when the helper returns None the caller falls through to the
    # generic regex sweep — which then leaks the strikethrough List price
    # and any sibling-card prices into ``price_hints``.  Pinned by the
    # 2026-05-09 paintball-pistol fixture (rating anchor at depth 10
    # yielded a 3-element price list including $219.99 List + $168.76
    # neighbour-card price).
    cur = node
    card = None
    for _ in range(25):
        parent = getattr(cur, "parent", None)
        if parent is None:
            break
        cls = (parent.attributes.get("class") or "")
        dct = (parent.attributes.get("data-component-type") or "")
        if "s-result-item" in cls or dct == "s-search-result":
            card = parent
            break
        cur = parent
    if card is None or not hasattr(card, "css"):
        return None

    for span in card.css("span.a-price"):
        cls = (span.attributes.get("class") or "")
        # Strikethrough variants: ``a-text-price`` is Amazon's class for
        # "List Price" displays; ``data-a-strike="true"`` is the explicit
        # marker. Either disqualifies this span as the buy-now price.
        if "a-text-price" in cls:
            continue
        if (span.attributes.get("data-a-strike") or "").lower() == "true":
            continue
        # Preferred path: ``a-offscreen`` carries fully-formed ``$NNN.NN``.
        for off in span.css("span.a-offscreen"):
            text = (off.text(separator=" ", strip=True) or "").strip()
            text = text.replace("\xa0", " ")
            m = re.search(r"\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)", text)
            if m:
                return m.group(1)
        # Fallback: no a-offscreen (stripped Sponsored render). Read split
        # markup directly: ``$ 429 . 00`` → ``429.00`` after canonicalising.
        raw = _canonicalize_prices(
            (span.text(separator=" ", strip=True) or "").strip()
        )
        m = re.search(r"\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)", raw)
        if m:
            return m.group(1)
    return None


def _ancestor_card_text(node: Any, max_hops: int = 6, max_text: int = 1500) -> str:
    """Walk up to ``max_hops`` parents and return the card-like ancestor's text.

    Phase 15 bumped from (4 hops, 600 chars) to (6 hops, 1500 chars) after
    fixtures like headphones.com showed product cards where the price lives
    in a sibling ``<div>`` that's 5 hops up from the title anchor — the old
    walk stopped at the anchor's immediate ``card__inner`` and never reached
    the price-bearing sibling. The wider window risks pulling in stray
    prices from neighbouring cards on tight grids; the LLM tier is the
    backstop that decides which price actually belongs to which title.
    """
    cur = node
    last_text = node.text(separator=" ", strip=True) if hasattr(node, "text") else ""
    for _ in range(max_hops):
        parent = getattr(cur, "parent", None)
        if parent is None:
            break
        text = parent.text(separator=" ", strip=True)
        if len(text) > max_text:
            return last_text
        last_text = text
        cur = parent
    return last_text


def _extract_shopify_embedded_products(html: str, base_url: str) -> list[dict[str, Any]]:
    """Extract candidate products embedded in Shopify / Bold JSON arrays.
    
    Resolves zero-match discovery failures on modern headless/SPA Shopify
    sites where product grids lack price hints in static HTML anchors.
    """
    results = []
    seen_canonicals = set()

    # Pattern 1: window.BOLD.subscriptions.addCachedProductData([...])
    for m in re.finditer(r"addCachedProductData\((\[.*\}])\);", html, re.DOTALL):
        try:
            products = json.loads(m.group(1))
            if isinstance(products, list):
                for p in products:
                    if not isinstance(p, dict):
                        continue
                    title = p.get("title") or p.get("name")
                    handle = p.get("handle") or p.get("url")
                    if not title or not handle or not isinstance(title, str) or not isinstance(handle, str):
                        continue
                    
                    handle_path = handle
                    if not handle.startswith("/") and not handle.startswith("http"):
                        handle_path = f"/products/{handle}"
                    url_abs = urljoin(base_url, handle_path)
                    parsed = urlparse(url_abs)
                    canonical = f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
                    if canonical in seen_canonicals:
                        continue

                    prices = []
                    base_price = p.get("price")
                    if isinstance(base_price, (int, float)) and base_price > 0:
                        if isinstance(base_price, int) or base_price.is_integer():
                            prices.append(f"{base_price / 100.0:.2f}")
                        else:
                            prices.append(f"{base_price:.2f}")

                    variants = p.get("variants")
                    if isinstance(variants, list):
                        for v in variants:
                            if isinstance(v, dict):
                                vp = v.get("price")
                                if isinstance(vp, (int, float)) and vp > 0:
                                    if isinstance(vp, int) or vp.is_integer():
                                        prices.append(f"{vp / 100.0:.2f}")
                                    else:
                                        prices.append(f"{vp:.2f}")
                    
                    seen_p = set()
                    deduped_prices = []
                    for pr in prices:
                        if pr not in seen_p:
                            seen_p.add(pr)
                            deduped_prices.append(pr)

                    if not deduped_prices:
                        continue

                    desc = p.get("description") or ""
                    desc_clean = re.sub(r"<[^>]+>", " ", desc)
                    desc_clean = re.sub(r"\s+", " ", desc_clean).strip()

                    context = f"{title} {desc_clean}"
                    if len(context) > 500:
                        context = context[:500] + "…"
                    context += " Variant prices: " + ", ".join(f"${pr}" for pr in deduped_prices)

                    seen_canonicals.add(canonical)
                    results.append({
                        "title": title.strip(),
                        "href_abs": url_abs,
                        "prices": deduped_prices,
                        "context": context,
                        "fx_approx": False,
                    })
        except Exception as exc:
            logger.debug(f"[universal_ai] Failed to parse addCachedProductData JSON: {exc}")

    # Pattern 2: Web Pixels Manager strings containing "productVariants":[...]
    for m in re.finditer(r"\"productVariants\"\s*:\s*(\[.*?\])(?:\}|,\s*\"|\Z)", html, re.DOTALL):
        raw_json = m.group(1)
        if '\\"' in raw_json:
            raw_json = raw_json.replace('\\"', '"').replace('\\\\/', '/').replace('\\/', '/')
        try:
            variants = json.loads(raw_json)
            if isinstance(variants, list):
                prod_map: dict[str, dict[str, Any]] = {}
                for v in variants:
                    if not isinstance(v, dict):
                        continue
                    prod = v.get("product")
                    if not isinstance(prod, dict):
                        continue
                    title = prod.get("title") or prod.get("untranslatedTitle")
                    url_val = prod.get("url")
                    if not title or not url_val or not isinstance(title, str) or not isinstance(url_val, str):
                        continue
                    
                    url_abs = urljoin(base_url, url_val)
                    parsed = urlparse(url_abs)
                    canonical = f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
                    if canonical in seen_canonicals:
                        continue

                    price_info = v.get("price")
                    amt = None
                    if isinstance(price_info, dict):
                        amt = price_info.get("amount")
                    elif isinstance(v.get("price"), (int, float)):
                        amt = v.get("price")
                    
                    if isinstance(amt, (int, float)) and amt > 0:
                        pr_str = f"{amt:.2f}"
                        if canonical not in prod_map:
                            prod_map[canonical] = {
                                "title": title.strip(),
                                "href_abs": url_abs,
                                "prices": [],
                                "context_parts": [title.strip()],
                            }
                        if pr_str not in prod_map[canonical]["prices"]:
                            prod_map[canonical]["prices"].append(pr_str)
                            v_title = v.get("title")
                            if v_title and isinstance(v_title, str) and v_title.strip():
                                prod_map[canonical]["context_parts"].append(f"Option {v_title.strip()}: ${pr_str}")

                for canonical, pdata in prod_map.items():
                    seen_canonicals.add(canonical)
                    context = " | ".join(pdata["context_parts"])
                    if len(context) > 500:
                        context = context[:500] + "…"
                    results.append({
                        "title": pdata["title"],
                        "href_abs": pdata["href_abs"],
                        "prices": pdata["prices"],
                        "context": context,
                        "fx_approx": False,
                    })
        except Exception as exc:
            logger.debug(f"[universal_ai] Failed to parse Web Pixels Manager productVariants JSON: {exc}")

    return results


def _extract_candidates(
    html: str, base_url: str, *, max_candidates: int = 80
) -> list[dict[str, Any]]:
    """Extract anchor-based product candidates from raw HTML.

    Each candidate has ``{idx, anchor_text, href, price_hints, context}``
    with ``href`` resolved to an absolute URL via ``urljoin``. The idx
    field is a stable integer the LLM echoes back — no URL ever round-trips
    through the LLM.

    Two-pass design (Phase 15): walk every anchor first into per-canonical
    URL groups, THEN merge and emit. This way a product card with TWO
    anchors at the same URL — one wrapping just the title (no price in its
    ancestor's text), one wrapping just the price — collapses into a single
    candidate that has both the title AND the price hints, instead of the
    old behaviour which kept whichever anchor came first in DOM order and
    dropped the other.
    """
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        logger.error("selectolax is required for universal_ai extraction.")
        return []

    tree = HTMLParser(html)
    if tree.body is None:
        return []

    base_host_is_amazon = "amazon." in urlparse(base_url).netloc.lower()

    # canonical_url -> {"href_abs": ..., "raw": [{title, context, prices}, ...]}
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []  # preserve DOM order of first encounter

    # Pre-seed groups with embedded Shopify products
    for ep in _extract_shopify_embedded_products(html, base_url):
        parsed = urlparse(ep["href_abs"])
        canonical = f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
        groups[canonical] = {"href_abs": ep["href_abs"], "raw": []}
        order.append(canonical)
        groups[canonical]["raw"].append({
            "title": ep["title"],
            "context": ep["context"],
            "prices": ep["prices"],
            "fx_approx": ep["fx_approx"],
        })

    for a in tree.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(_SKIP_HREF_PREFIXES):
            continue

        href_abs = urljoin(base_url, href)
        parsed = urlparse(href_abs)
        if parsed.scheme not in ("http", "https"):
            continue

        if _is_search_or_category_url(href_abs):
            continue
        if _looks_like_nav_path(href_abs):
            continue

        title = _anchor_title(a)
        if title.lower() in _UI_CHROME_TEXTS:
            continue

        # Save raw (pre-stripped) context for foreign currency detection.
        raw_context = _canonicalize_prices(_ancestor_card_text(a))
        context = _strip_foreign_currencies(raw_context)
        prices = _PRICE_PATTERN.findall(context)

        # Amazon: override hints with the card's primary buy-now price
        # (first non-strikethrough <span class="a-price"> > a-offscreen).
        # Stops the LLM picking up "List:" strikethrough or "From: $X"
        # used-condition sub-prices that the wide ancestor walk drags in.
        #
        # Phase 19b: when the helper returns None (no span.a-price in the
        # card — e.g. "See options" variant cards from Amazon's European
        # locale), try to convert any foreign-currency amount in the raw
        # context to approximate USD.  Better to show an approximate price
        # than to drop the listing entirely.
        fx_approx: bool | str
        if base_host_is_amazon:
            az_price = _amazon_card_primary_price(a)
            if az_price is not None:
                prices = [az_price]
                fx_approx = False
            else:
                fx_res = _foreign_price_to_usd(raw_context)
                if fx_res is not None:
                    fx_usd, fx_code = fx_res
                    prices = [f"{fx_usd:.2f}"]
                    fx_approx = fx_code
                    # Mark the context so downstream can flag it.
                    context = context.rstrip() + f" [price approx. from {fx_code}]"
                else:
                    fx_approx = False
        else:
            fx_approx = False

        canonical = (
            f"{parsed.scheme}://{parsed.netloc.lower()}"
            f"{parsed.path.rstrip('/')}"
        )
        if canonical not in groups:
            groups[canonical] = {"href_abs": href_abs, "raw": []}
            order.append(canonical)
        groups[canonical]["raw"].append({
            "title": title,
            "context": context,
            "prices": prices,
            "fx_approx": fx_approx,
        })

    # Merge per-canonical groups into final candidates.
    candidates: list[dict[str, Any]] = []
    for canonical in order:
        bucket = groups[canonical]
        raw = bucket["raw"]
        href_abs = bucket["href_abs"]

        # Pick the longest non-empty title — wins over empty (image-only)
        # anchors and over very short labels like "View".
        non_empty_titles = [r["title"].strip() for r in raw if r["title"].strip()]
        best_title = max(non_empty_titles, key=len) if non_empty_titles else ""

        # Merge price hints across all anchors in the group, dedupe, preserve order.
        merged_prices: list[str] = []
        seen_prices: set[str] = set()
        for r in raw:
            for p in r["prices"]:
                if p not in seen_prices:
                    seen_prices.add(p)
                    merged_prices.append(p)

        # Use the longest context — typically the one whose ancestor
        # encloses both title and price subtrees.
        best_context = max((r["context"] for r in raw), key=len, default="")

        # An anchor needs SOME signal that it's a product:
        # either the URL itself looks product-like, or there's a price nearby.
        if not (_looks_like_product_url(href_abs) or merged_prices):
            continue

        # Drop entirely-empty rows (no title, no price, near-empty context):
        # nothing for the LLM to act on.
        if not best_title and not merged_prices and len(best_context) < 20:
            continue

        # Trim context aggressively for token economy; the LLM doesn't
        # need the whole card, just enough to read title + price.
        if len(best_context) > 400:
            best_context = best_context[:400] + "…"

        candidates.append({
            "idx": len(candidates),
            "anchor_text": best_title[:240],
            "href": href_abs,
            "price_hints": [f"${p}" for p in merged_prices[:5]],
            "context": best_context,
            "fx_approx": any(r.get("fx_approx") for r in raw),
        })

        if len(candidates) >= max_candidates:
            break

    return candidates


# --- Tier 1.5: single-product detail-page extractor (ADR-049) --------------


# Per ADR-049 we strip script/style/nav/header/footer; noscript/template/
# svg/iframe carry no product text so they go too (token economy). NOTE:
# do NOT add ``form`` here — many storefronts (Odoo/Wiredzone, others) put
# the price + Add-to-Cart inside the product <form>; decomposing it deletes
# the very price Tier 1.5 needs.
_DETAIL_STRIP_TAGS = (
    "script", "style", "noscript", "template", "svg",
    "nav", "header", "footer", "iframe",
)

# Hard cap on the stripped text we hand the LLM. Detail pages with long spec
# tables / reviews can balloon; ~16k chars keeps the single Haiku call cheap
# and bounded while still containing the price + title (which are near the
# top of every real product page).
_DETAIL_MAX_CHARS = 16000


def _strip_to_main_text(html: str) -> str:
    """Reduce a product-detail page to its visible main-content text.

    Drops ``<script>/<style>/<nav>/<header>/<footer>`` etc., flattens the
    remaining body to whitespace-collapsed text, canonicalises Amazon-style
    split price spans, and strips foreign-currency amounts (AlterLab's
    European exit IPs leak EUR/GBP into the DOM). The result is BOTH the
    LLM payload AND the haystack the price-verbatim guard checks against,
    so the guard can never disagree with what the model saw.
    """
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        logger.error("selectolax is required for universal_ai detail extraction.")
        return ""

    tree = HTMLParser(html)
    if tree.body is None:
        return ""
    for sel in _DETAIL_STRIP_TAGS:
        for node in tree.css(sel):
            node.decompose()

    text = tree.body.text(separator=" ", strip=True) or ""
    text = re.sub(r"\s+", " ", text).strip()
    text = _canonicalize_prices(text)
    text = _strip_foreign_currencies(text)
    if len(text) > _DETAIL_MAX_CHARS:
        text = text[:_DETAIL_MAX_CHARS]
    return text


def _price_in_text(price: float, text: str) -> bool:
    """True iff ``price`` occurs verbatim in ``text`` under normalisation.

    The architectural guard (ADR-001): the LLM never produces a price the
    deterministic layer didn't fetch. We strip ``$``, commas and whitespace
    from the haystack and look for the price in its common printed forms
    (``2335.00`` / ``2335`` / ``2,335.00`` all normalise the same way), so a
    fabricated number is dropped while real formatting variation passes.
    """
    norm = re.sub(r"[\s,$]", "", text)
    forms: set[str] = {f"{price:.2f}"}
    if price == int(price):
        forms.add(str(int(price)))
        forms.add(f"{int(price)}.00")
    # No-trailing-zero form, e.g. 2335.5 -> "2335.5".
    trimmed = (f"{price:.2f}").rstrip("0").rstrip(".")
    if trimmed:
        forms.add(trimmed)
    return any(f and f in norm for f in forms)


def _resolve_detail_mode(query: AdapterQuery, url: str) -> str:
    """Return ``"detail"``, ``"search"``, or ``"auto"`` for ``url``.

    Explicit ``page_type`` on the profile source wins (deterministic
    opt-in, preferred). When absent, fall back to the URL-shape heuristic:
    a product-looking URL that is NOT a search/category URL is treated as
    a detail page (``"auto"`` — Tier 1.5 is attempted but the anchor tier
    still runs as a fallback if it yields nothing, so the heuristic can't
    regress existing search pages).
    """
    page_type = query.extra.get("page_type")
    if page_type in ("detail", "search"):
        return str(page_type)
    if _looks_like_product_url(url) and not _is_search_or_category_url(url):
        return "auto"
    return "search"


def _extract_detail_listing(
    html: str,
    url: str,
    *,
    profile: Any | None,
    fetched_at: datetime,
    parsed_host: str,
) -> list[Listing]:
    """Tier 1.5: extract the single product from a detail page, or [].

    One bounded ``claude-haiku-4-5`` call against the stripped page text.
    The returned price is re-verified verbatim against that same text
    before a Listing is emitted; the URL is ALWAYS the source URL (never
    LLM-produced). Returns ``[]`` (caller decides whether to fall through
    to the anchor tier) when nothing extractable is found.
    """
    global LAST_RUN_USAGE

    text = _strip_to_main_text(html)
    if len(text) < 20:
        logger.info(
            f"[universal_ai] Tier 1.5: stripped body too small "
            f"({len(text)} chars) for {url}; skipping detail extraction."
        )
        return []

    try:
        resp = call_llm(
            provider="anthropic",
            model="claude-haiku-4-5",
            system=DETAIL_SYSTEM_PROMPT,
            messages=[Message(role="user", content=text)],
            response_format="json",
            max_tokens=1024,
        )
    except Exception as exc:
        logger.error(
            f"[universal_ai] Tier 1.5 LLM call failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return []

    LAST_RUN_USAGE = {
        "step": "universal_ai_search",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
    }

    parsed = _extract_json(resp.text or "")
    if not isinstance(parsed, dict):
        logger.warning(
            f"[universal_ai] Tier 1.5: unparseable detail response for {url}: "
            f"{(resp.text or '')[:200]}"
        )
        return []

    if not parsed.get("found"):
        logger.info(
            f"[universal_ai] Tier 1.5: model found no priced product on {url}."
        )
        return []

    raw_price = parsed.get("price_usd")
    try:
        price = float(raw_price)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []
    if price <= 0:
        return []

    if not _price_in_text(price, text):
        logger.warning(
            f"[universal_ai] Tier 1.5: extracted price {price} NOT found "
            f"verbatim in fetched body for {url}; dropping (anti-hallucination "
            f"guard, ADR-001)."
        )
        return []

    title = str(parsed.get("title") or "").strip()
    if not title:
        return []

    condition = str(parsed.get("condition") or "new").strip().lower()
    if condition not in ("new", "used", "refurbished"):
        condition = "new"

    try:
        llm_pack_size = int(parsed.get("pack_size") or 1)
    except (TypeError, ValueError):
        llm_pack_size = 1
    is_kit, kit_module_count, unit_price_usd, kit_price_usd = _parse_pack(
        title, price, llm_pack_size=llm_pack_size
    )

    # in_stock: False → quantity_available 0 so the in_stock filter
    # (reject when qty <= 0) works; True/absent → None (unknown).
    in_stock = parsed.get("in_stock")
    quantity_available = 0 if in_stock is False else None

    logger.info(
        f"[universal_ai] Tier 1.5: extracted 1 detail listing from {url} "
        f"(${price:.2f}, {condition})."
    )
    return [
        Listing(
            source="universal_ai_search",
            url=url,
            title=title[:300],
            fetched_at=fetched_at,
            brand=None,
            mpn=None,
            attrs={"vendor_host": parsed_host, "extractor": "detail_llm"},
            condition=condition,
            is_kit=is_kit,
            kit_module_count=kit_module_count,
            unit_price_usd=unit_price_usd,
            kit_price_usd=kit_price_usd,
            quantity_available=quantity_available,
            seller_name=parsed_host,
            seller_rating_pct=None,
            seller_feedback_count=None,
            ship_from_country=None,
        )
    ]


# --- Main entry point ------------------------------------------------------


def fetch(query: AdapterQuery, profile: Any | None = None) -> list[Listing]:
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

    alterlab_options = query.extra.get("alterlab_options")
    if not isinstance(alterlab_options, dict):
        alterlab_options = None

    logger.info(f"[universal_ai] Fetching {url}")
    try:
        html, status, fetcher = _fetch_html_with_retry(url, alterlab_options=alterlab_options)
    except Exception as exc:
        logger.error(f"[universal_ai] Fetch failed: {type(exc).__name__}: {exc}")
        # Bubble up explicit fetch errors (like AlterLab quota/auth) so cli.py
        # can surface them in the UI.
        raise

    logger.info(
        f"[universal_ai] Fetched via {fetcher}: status={status}, "
        f"body_len={len(html)} chars"
    )
    if not html:
        logger.warning(f"[universal_ai] Empty body for {url}.")
        return []

    fetched_at = datetime.now(tz=UTC)
    parsed_host = urlparse(url).netloc.lower()

    # Phase 15 tier 1: JSON-LD. Free, deterministic, no LLM call.
    jsonld_listings = _extract_jsonld_listings(html, base_url=url)
    if jsonld_listings:
        logger.info(
            f"[universal_ai] Extracted {len(jsonld_listings)} listing(s) from "
            f"JSON-LD on {url}; skipping anchor/LLM tier."
        )
        jsonld_results: list[Listing] = []
        for item in jsonld_listings:
            is_kit, kit_module_count, unit_price_usd, kit_price_usd = _parse_pack(
                item["title"], item["price_usd"]
            )
            jsonld_results.append(
                Listing(
                    source="universal_ai_search",
                    url=item["url"],
                    title=item["title"],
                    fetched_at=fetched_at,
                    brand=None,
                    mpn=None,
                    attrs={"vendor_host": parsed_host, "extractor": "jsonld"},
                    condition=item["condition"],
                    is_kit=is_kit,
                    kit_module_count=kit_module_count,
                    unit_price_usd=unit_price_usd,
                    kit_price_usd=kit_price_usd,
                    quantity_available=None,
                    seller_name=parsed_host,
                    seller_rating_pct=None,
                    seller_feedback_count=None,
                    ship_from_country=None,
                )
            )
        return jsonld_results

    # Tier 1.5 (ADR-049): single-product detail-page extractor. Runs after
    # JSON-LD found nothing, only for detail-flagged / detail-shaped URLs.
    detail_mode = _resolve_detail_mode(query, url)
    if detail_mode != "search":
        detail_listings = _extract_detail_listing(
            html, url,
            profile=profile,
            fetched_at=fetched_at,
            parsed_host=parsed_host,
        )
        if detail_listings:
            return detail_listings
        if detail_mode == "detail":
            # Explicit opt-in: the page IS one product; the anchor tier
            # would only emit nav junk. Don't burn a second LLM call.
            logger.info(
                f"[universal_ai] Tier 1.5 yielded nothing for explicit "
                f"detail source {url}; not falling through to anchor tier."
            )
            return []
        # detail_mode == "auto" (URL-shape heuristic): fall through to the
        # anchor tier so a real search/category page is never regressed.

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

    prompt = SYSTEM_PROMPT
    extra_keys = []
    if profile and hasattr(profile, "spec_attrs") and profile.spec_attrs:
        extra_lines = []
        for k, attr_def in profile.spec_attrs.items():
            extra_keys.append(k)
            attr_type = getattr(attr_def, "type", "str")
            desc = f'  - "{k}": extract this attribute from title or context if present (type: {attr_type}).'
            if hasattr(attr_def, "enum") and attr_def.enum:
                desc += f" Must be one of: {attr_def.enum}."
            extra_lines.append(desc)
        if extra_lines:
            prompt = prompt.replace(
                "Output a JSON object:",
                "\n".join(extra_lines) + "\n\nOutput a JSON object:",
            )

    try:
        resp = call_llm(
            provider="anthropic",
            model="claude-haiku-4-5",
            system=prompt,
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

        attrs = {"vendor_host": parsed_host, "extractor": "anchor_llm"}
        if cand.get("fx_approx"):
            attrs["price_approx_fx"] = cand["fx_approx"]

        for k in extra_keys:
            if k in v and v[k] is not None:
                attrs[k] = v[k]

        try:
            llm_pack_size = int(v.get("pack_size") or 1)
        except (TypeError, ValueError):
            llm_pack_size = 1

        is_kit, kit_module_count, unit_price_usd, kit_price_usd = _parse_pack(
            title, price, llm_pack_size=llm_pack_size
        )

        results.append(Listing(
            source="universal_ai_search",
            url=cand["href"],
            title=title[:300],
            fetched_at=fetched_at,
            brand=None,
            mpn=None,
            attrs=attrs,
            condition=condition,
            is_kit=is_kit,
            kit_module_count=kit_module_count,
            unit_price_usd=unit_price_usd,
            kit_price_usd=kit_price_usd,
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
