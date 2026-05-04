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
    alterlab_key = os.environ.get("ALTERLAB_API_KEY", "").strip()
    if alterlab_key:
        # AlterLab needs its own (much longer) timeout: render_js spins up
        # a real Chrome and can take 30-60s on heavy pages (B&H, Crutchfield).
        # The outer `timeout` arg is sized for the cheap raw-HTTP fetchers and
        # would prematurely abort an in-flight render.
        try:
            return _fetch_via_alterlab(url, alterlab_key, timeout=120.0)
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
        resp = client.get(url)
        return resp.text or "", resp.status_code, "httpx"


def _fetch_via_alterlab(
    url: str, api_key: str, *, timeout: float = 60.0
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
        "advanced": {"render_js": True},
    }
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
    cur = node
    card = None
    for _ in range(10):
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
        for off in span.css("span.a-offscreen"):
            text = (off.text(separator=" ", strip=True) or "").strip()
            text = text.replace(" ", " ")
            m = re.search(r"\$\s*(\d{1,4}(?:,\d{3})*(?:\.\d{2})?)", text)
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

        context = _canonicalize_prices(_ancestor_card_text(a))
        prices = _PRICE_PATTERN.findall(context)

        # Amazon: override hints with the card's primary buy-now price
        # (first non-strikethrough <span class="a-price"> > a-offscreen).
        # Stops the LLM picking up "List:" strikethrough or "From: $X"
        # used-condition sub-prices that the wide ancestor walk drags in.
        if base_host_is_amazon:
            az_price = _amazon_card_primary_price(a)
            if az_price is not None:
                prices = [az_price]

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
        return [
            Listing(
                source="universal_ai_search",
                url=item["url"],
                title=item["title"],
                fetched_at=fetched_at,
                brand=None,
                mpn=None,
                attrs={"vendor_host": parsed_host, "extractor": "jsonld"},
                condition=item["condition"],
                is_kit=False,
                kit_module_count=1,
                unit_price_usd=item["price_usd"],
                kit_price_usd=None,
                quantity_available=None,
                seller_name=parsed_host,
                seller_rating_pct=None,
                seller_feedback_count=None,
                ship_from_country=None,
            )
            for item in jsonld_listings
        ]

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
            attrs={"vendor_host": parsed_host, "extractor": "anchor_llm"},
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
