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
import threading
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import (
    parse_qs,
    quote_plus,
    unquote_plus,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

from product_search.llm import Message, call_llm
from product_search.models import AdapterQuery, Listing
from product_search.source_reasons import THIN_BODY_CEILING, WATCH_GATE_REASON_PREFIX
from product_search.vendor_quirks import (
    apply_url_transforms,
    get_quirks_for_url,
    merge_alterlab_options,
    normalize_alterlab_options,
)

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
  - "pack_size": integer. ONLY set > 1 when the listing sells multiple
    units of the SAME product (true homogeneous multi-packs: "2-pack",
    "5 pack", "6 count", "8x32GB", "kit of 4"). If the listing is one
    product bundled with a DIFFERENT accessory (e.g. "headphones + stand",
    "camera + lens", "console + game"), pack_size MUST be 1 — the base
    product is still one unit. Default to 1.
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
  - **Subscription / multi-issue offers**: if the page offers multiple
    subscription terms for the same product (single issue, monthly,
    quarterly, semi-annual, annual / 1-year / 2-year), pick the LONGEST
    term offered and set ``price_usd`` to that term's total price and
    ``pack_size`` to the number of issues / months included (e.g. annual
    magazine subscription = 12 monthly issues → pack_size 12; weekly
    magazine annual = 52 issues → pack_size 52). Picking the single-issue
    or per-month price for a product that is sold as a longer subscription
    misrepresents the as-sold cost (the 2026-05-25 pocketmags case:
    annual sub $159.99 was missed in favor of $3.99 single-issue —
    ADR-094 D3). When only one term is offered, use it as-is with the
    matching pack_size.
  - Output JSON ONLY. No prose preamble, no markdown fences.
"""


# Tier 2.5 (Phase 21 / ADR-077): recall-first full-HTML search extractor.
# The anchor walker (`_extract_candidates`) gates the candidate set on its
# structural heuristics (price-near-anchor / product-shaped URL / nav filter /
# cap 80), so any product whose markup doesn't fit silently never reaches the
# filter. This tier instead hands Haiku the WHOLE stripped page text plus a
# numbered list of EVERY link on the page, and asks it to enumerate all
# products, mapping each to a link by index — URLs are still chosen by index
# (never typed by the LLM) and the price is re-verified verbatim against the
# fetched text, so the no-fabrication boundary (ADR-001) is preserved. Its
# results are UNIONed (dedupe-by-canonical-URL) with the JSON-LD tier and the
# anchor walker — purely additive recall, never a replacement.
SEARCH_FULL_HTML_SYSTEM_PROMPT = """You are enumerating EVERY product for \
sale on one vendor search / category / collection page.

The user message has two parts:
  1. PAGE TEXT — the visible text of the whole page (scripts, nav, header and
     footer already stripped), in reading order. Product titles and prices
     both appear here, though a product's price may sit just before or just
     after its title.
  2. LINKS — a numbered list of every hyperlink on the page, one per line as
     "idx: link text". Each product's clickable title or image is one of
     these links.

Your job: list ALL distinct products offered for sale on this page. Be
exhaustive — do NOT stop early and do NOT skip products that look similar to
each other. For each product return:
  - "title": the product title as shown on the page.
  - "price_usd": numeric only (e.g. 249.99). The CURRENT selling price for
    THAT product, copied EXACTLY from PAGE TEXT (ignore the currency symbol
    and thousands separators). If you cannot find a price for a product in
    PAGE TEXT, OMIT that product.
  - "condition": one of "new", "used", "refurbished" (default "new").
  - "pack_size": integer — units sold in one purchase (default 1; set > 1
    ONLY for true homogeneous multi-packs like "2-pack" / "kit of 4").
  - "link_idx": the idx of the link from LINKS whose text best matches this
    product's title. If no link plausibly matches, OMIT the product (a product
    with no URL cannot be recorded).

Hard rules:
  - The price MUST appear verbatim in PAGE TEXT. NEVER invent, estimate,
    round, or currency-convert a price.
  - Use the current buy price — NOT a list/MSRP/strikethrough/"was"/"reg"
    price, NOT a bundled-accessory price, NOT a financing installment.
  - "condition" stays "new" unless the page explicitly says otherwise.
  - Enumerate generously: a real search page typically lists 10-50 products.
    Missing a real product is the worst error you can make here.
  - Use ONLY idx values that appear in LINKS — never invent a link_idx.
  - Output a JSON object {"products": [...]} ONLY. No prose, no markdown fences.
"""


# --- Module-level capture for cli.py's run-cost panel -----------------------

tls = threading.local()


def _accumulate_usage(resp: Any) -> None:
    """Fold one LLM call's token usage into ``tls.last_run_usage``.

    A single ``fetch()`` can now make several Haiku calls (anchor tier +
    full-HTML tier, possibly chunked). The cost panel reads one usage record
    per source, so we SUM input/output tokens across every call in the fetch
    rather than letting the last call clobber the earlier ones.
    """
    prev = getattr(tls, "last_run_usage", None) or {
        "step": "universal_ai_search",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "input_tokens": 0,
        "output_tokens": 0,
    }
    tls.last_run_usage = {
        "step": "universal_ai_search",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "input_tokens": int(prev.get("input_tokens", 0)) + int(resp.input_tokens),
        "output_tokens": int(prev.get("output_tokens", 0)) + int(resp.output_tokens),
    }


# --- HTTP fetch with TLS impersonation -------------------------------------


def _fetch_html(
    url: str,
    timeout: float = 20.0,
    alterlab_options: dict[str, Any] | None = None,
) -> tuple[str, int, str]:
    """Fetch ``url`` and return ``(html, status_code, fetcher_label)``.

    Four-tier fetch strategy:

    1. **Scrappey** — when ``SCRAPPEY_API_KEY`` is set AND the vendor's
       ``alterlab_options`` include ``use_scrappey: true``. Routes through
       the Scrappey browser-render API with a US residential proxy.
       Bypasses Cloudflare Turnstile/WAF on walled vendors (ADR-103).
       Pay-as-you-go (EUR 1/1K browser requests, no subscription).

    2. **AlterLab** — rendered fetch with residential proxies and JS
       execution. Gets past most bot walls except aggressive CF on
       datacenter-banned vendors.

    3. **curl_cffi** — Chrome TLS fingerprint impersonation. Free, fast,
       beats basic Cloudflare TLS-fingerprint blocks but does no JS
       execution. Works on most server-rendered storefronts.

    4. **httpx** — plain HTTP fallback when ``curl_cffi`` isn't installed.
       Default Python TLS fingerprint, fails on most modern bot detection.

    Either way the response body is returned verbatim — non-2xx status
    codes are logged but the body is still returned because some sites
    serve a challenge page with status 200 and others 403.
    """
    # ── Tier 1: Scrappey (CF-walled vendors only) ──────────────────────
    scrappey_key = os.environ.get("SCRAPPEY_API_KEY", "").strip()
    use_scrappey = alterlab_options and alterlab_options.get("use_scrappey")
    if scrappey_key and use_scrappey:
        proxy_country = (alterlab_options or {}).get("proxy_country", "UnitedStates")
        try:
            render_js = (alterlab_options or {}).get("render_js", False)
            return _fetch_via_scrappey(url, scrappey_key, proxy_country, render_js=render_js)
        except Exception as exc:
            logger.warning(
                "[universal_ai] Scrappey fetch failed "
                f"({type(exc).__name__}: {exc}); falling through to "
                "AlterLab/curl_cffi/httpx."
            )

    # ── Tier 2: AlterLab ──────────────────────────────────────────────
    alterlab_key = os.environ.get("ALTERLAB_API_KEY", "").strip()
    skip_alterlab = alterlab_options and alterlab_options.get("skip_alterlab")
    if alterlab_key and not skip_alterlab:
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
            if hasattr(exc, "response") and exc.response.status_code == 422:
                logger.error(f"[universal_ai] AlterLab 422 Response Body: {exc.response.text}")
            # Check if quota/auth error and bubble it up instead of fallback
            if hasattr(exc, "response") and exc.response.status_code in (401, 403, 429):
                # We raise so fetch() catches it and bubbles up to cli.py
                raise RuntimeError(f"AlterLab API issue: HTTP {exc.response.status_code} quota or auth error") from exc
            # Don't let an AlterLab outage zero a run — fall through to
            # Scrappey (if available) or the cheap tiers. The worker log
            # captures the failure so repeated outages are debuggable.
            if scrappey_key and not use_scrappey:
                logger.warning(
                    f"[universal_ai] AlterLab fetch failed ({type(exc).__name__}: "
                    f"{exc}); dynamically falling back to Scrappey."
                )
                try:
                    proxy_country = (alterlab_options or {}).get("proxy_country", "UnitedStates")
                    render_js = (alterlab_options or {}).get("render_js", False)
                    return _fetch_via_scrappey(
                        url,
                        scrappey_key,
                        proxy_country,
                        triggered_by="dynamic_weak_render_fallback",
                        render_js=render_js,
                    )
                except Exception as sc_exc:
                    logger.warning(
                        "[universal_ai] Dynamic Scrappey fallback failed "
                        f"({type(sc_exc).__name__}: {sc_exc}); falling back to curl_cffi/httpx."
                    )
            else:
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

    cc_requests: Any = None
    try:
        from curl_cffi import requests as _cc

        cc_requests = _cc
    except ImportError:
        pass

    if cc_requests is not None:
        try:
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
        except Exception as exc:
            # The documented cascade (see _fetch_html docstring) is
            # curl_cffi -> httpx, but originally only ImportError was caught,
            # so any transport-level curl_cffi failure propagated out and the
            # httpx tier never ran. Observed 2026-05-25 on a Best Buy detail
            # URL: AlterLab returned a non-retryable 4xx, the cascade dropped
            # to curl_cffi, which raised
            #   "HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR (err 2)"
            # and the source died with zero listings even though httpx would
            # have been the right next attempt. Catching keeps the cascade
            # honest at no extra AlterLab cost.
            logger.warning(
                "[universal_ai] curl_cffi fetch failed "
                f"({type(exc).__name__}: {exc}); falling through to httpx."
            )

    import httpx

    with httpx.Client(follow_redirects=True, headers=headers, timeout=timeout) as client:
        httpx_resp = client.get(url)
        return httpx_resp.text or "", httpx_resp.status_code, "httpx"


# ── Scrappey browser-render fetch (ADR-103) ─────────────────────────────────


def _fetch_via_scrappey(
    url: str,
    api_key: str,
    proxy_country: str = "UnitedStates",
    triggered_by: str = "tier1_configured",
    render_js: bool = False,
) -> tuple[str, int, str]:
    """Fetch *url* through the Scrappey browser-render API.

    Scrappey spins up a real browser (Firefox) on a residential proxy and
    returns the fully-rendered DOM. This bypasses Cloudflare Turnstile,
    DataDome, and similar JS-challenge bot walls that block datacenter IPs.

    Cost: ~EUR 1.00 per 1,000 browser requests (PAYG, no subscription).
    Only successful requests are billed.
    """
    import httpx

    api_url = f"https://publisher.scrappey.com/api/v1?key={api_key}"
    payload = {
        "cmd": "request.get",
        "url": url,
        "proxyCountry": proxy_country,
        "proxyType": "residential",
        "browser": render_js,
    }

    start_time = time.time()
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            api_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()

    data = resp.json()

    # Scrappey wraps the page body in {"solution": {"response": "...", ...}}
    solution = data.get("solution", {})
    body = solution.get("response", "")
    origin_status = solution.get("statusCode", 200)
    ip_info = solution.get("ipInfo", {})
    
    elapsed_ms = int((time.time() - start_time) * 1000)

    # Log the exit-IP country so we can debug geo-blocks.
    exit_ip = ip_info.get("query", "?")
    exit_country = ip_info.get("country", "?")
    is_hosting = ip_info.get("hosting", False)
    logger.info(
        "[universal_ai] Scrappey fetch: %s → %d chars, "
        "exit_ip=%s (%s, hosting=%s)",
        url[:80],
        len(body),
        exit_ip,
        exit_country,
        is_hosting,
    )

    # Check for Scrappey-level errors (DNS failures, timeouts, etc.)
    if data.get("error"):
        error_msg = str(data["error"])
        # Short body + error ⇒ the fetch genuinely failed.
        if len(body) < _WEAK_BODY_FLOOR:
            raise RuntimeError(f"Scrappey error: {error_msg[:200]}")
        # Long body + error ⇒ Scrappey logged a warning but the page
        # rendered. Treat it as a success (the body is usable).
        logger.warning(
            "[universal_ai] Scrappey reported error but body is %d chars "
            "(keeping): %s",
            len(body),
            error_msg[:200],
        )

    cf_challenge = bool(_WEAK_RENDER_SIGNATURES.search(body)) if body else False
    if not hasattr(tls, "scrappey_diagnostics"):
        tls.scrappey_diagnostics = []

    tls.scrappey_diagnostics.append({
        "url": url[:80],
        "body_len": len(body),
        "origin_status": int(origin_status) if origin_status else 200,
        "exit_ip": exit_ip,
        "exit_country": exit_country,
        "exit_hosting": is_hosting,
        "cf_challenge": cf_challenge,
        "triggered_by": triggered_by,
        "elapsed_ms": elapsed_ms,
    })

    return body, int(origin_status) if origin_status else 200, "scrappey"


# ADR-078 (R1): how many times to retry the AlterLab API on a transient 5xx
# before letting the error propagate to the curl_cffi/httpx fallback. Linear
# backoff (n * base) keeps the worst case bounded (~2+4 = 6s of waiting) while
# giving a pool-exhausted AlterLab a real chance to recover.
_ALTERLAB_5XX_MAX_ATTEMPTS = 3
_ALTERLAB_5XX_BACKOFF_SECONDS = 2.0

# ADR-083: a 422 is normally a "wrong request shape" that a retry can't fix
# (so ADR-078 raises it immediately). But `browser_pool_exhausted` is a 422
# that is semantically a *transient capacity* error — AlterLab's upstream
# Chrome pool has no free slot — and a backoff CAN clear it. We special-case
# those 422s and route them through the same bounded-retry path as a 5xx, with
# a LONGER backoff (pool exhaustion typically outlasts the 5xx 2+4s window).
# All other 422s, and 401/403/429, still raise immediately.
_ALTERLAB_422_TRANSIENT_MARKERS = ("browser_pool_exhausted",)
_ALTERLAB_POOL_BACKOFF_SECONDS = 5.0


def _is_transient_alterlab_422(resp: Any) -> bool:
    """True if a 422 response body names a transient (retryable) AlterLab error.

    Robust to body shape — matches the marker against the raw text rather than
    assuming a particular JSON envelope.
    """
    try:
        body = (resp.text or "").lower()
    except Exception:
        return False
    return any(marker in body for marker in _ALTERLAB_422_TRANSIENT_MARKERS)


def _build_alterlab_body(
    url: str,
    alterlab_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the AlterLab POST body in the DOCUMENTED nested shape (ADR-071).

    Pure (no I/O) so the T5 parity guard can assert it byte-for-byte equals the
    TS ``buildAlterlabBody`` in ``web/lib/onboard/alterlab-shared.ts`` for the
    same options — the systemic anti-drift fix that would have caught the
    missing ``asp`` (ADR-070) instantly. The flat internal keys
    (``country`` / ``min_tier`` / ``wait_condition`` / ``render_js`` / ``asp``)
    are mapped onto ``location`` / ``cost_controls.max_tier`` / ``advanced.*``.

    Defensive normalization runs here (ADR-071): callers that skip vendor_quirks
    (``skip_vendor_quirks``, the CLI probe) reach this with raw options, and
    ``wait_for`` is a non-existent AlterLab param that silently zeros the body
    via a never-completing 202 job — this is the last line of defence before it
    would hit the wire.
    """
    advanced: dict[str, Any] = {"render_js": True}
    body: dict[str, Any] = {
        "url": url,
        "sync": True,
        "formats": ["html"],
        "asp": True,
        "advanced": advanced,
    }
    opts = normalize_alterlab_options(alterlab_options)
    if opts:
        if opts.get("country"):
            body["location"] = {"country": opts["country"]}
        if opts.get("min_tier") is not None:
            # max_tier escalates UP TO this tier from cheap, returning a fast
            # sync 200 — the reliable "use tier 4 if needed" knob. String per docs.
            tier = max(1, min(4, int(opts["min_tier"])))
            body["cost_controls"] = {"max_tier": str(tier)}
        if opts.get("wait_condition") is not None:
            advanced["wait_condition"] = opts["wait_condition"]
        if opts.get("render_js") is not None:
            advanced["render_js"] = opts["render_js"]
        if opts.get("asp") is not None:
            body["asp"] = opts["asp"]
    return body


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

    Wire format — the DOCUMENTED body shape (ADR-071, docs/ALTERLAB_OPTIONS.md):
      POST https://api.alterlab.io/api/v1/scrape
      Header: X-API-Key: <key>
      Body:   {"url": ..., "sync": true, "formats": ["html"], "asp": true,
               "location": {"country": "us"},
               "cost_controls": {"max_tier": "4"},
               "advanced": {"render_js": true, "wait_condition": "networkidle"}}
      Resp:   {"status_code": <origin>, "content": {"html": "..."} | "..."}

    The internal options dict still uses the flat keys ``country`` / ``min_tier``
    / ``wait_condition`` (the registry + source representation); they are mapped
    here to the documented nested wire fields. The R2 matrix proved this shape
    is reliable (Target detail 3/3 with ``$249.99``) where the legacy flat
    ``country``/``min_tier`` shape 202-hung (0/3), and that ``min_tier`` mapped
    to ``cost_controls.max_tier`` returns a fast sync 200 — unlike the legacy
    top-level ``min_tier:4`` that always 202-hangs (ADR-071).

    ``formats: ["html"]`` makes ``content`` deterministically an object
    with an ``html`` field (vs a bare string in some sync responses).
    """
    import httpx

    body = _build_alterlab_body(url, alterlab_options)

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    api = "https://api.alterlab.io/api/v1/scrape"
    payload: dict[str, Any] = {}
    with httpx.Client(timeout=timeout) as client:
        for attempt in range(1, _ALTERLAB_5XX_MAX_ATTEMPTS + 1):
            resp = client.post(api, json=body, headers=headers)

            if resp.status_code == 202:
                payload = resp.json()
                job_id = payload.get("job_id")
                if job_id:
                    start_time = time.time()
                    while time.time() - start_time < timeout:
                        time.sleep(3)
                        job_resp = client.get(f"https://api.alterlab.io/api/v1/jobs/{job_id}", headers=headers)
                        if job_resp.status_code == 200:
                            job_payload = job_resp.json()
                            if job_payload.get("status") in ("completed", "failed"):
                                payload = job_payload.get("result", {})
                                break
                break

            # ADR-078 (R1): retry AlterLab ITSELF on a transient 5xx (gateway
            # timeout / pool exhaustion) with bounded backoff BEFORE the caller
            # drops to curl_cffi. The R2/R3 eval (2026-05-24) showed a degraded-
            # but-recoverable AlterLab 504 silently fell to a no-JS/no-proxy tier
            # that every bot-walled retailer blocks, zeroing recall. A retry at
            # the rendered tier is the right response; only after exhausting it do
            # we let the error propagate to the curl_cffi/httpx fallback.
            if resp.status_code >= 500 and attempt < _ALTERLAB_5XX_MAX_ATTEMPTS:
                logger.warning(
                    f"[universal_ai] AlterLab API HTTP {resp.status_code} "
                    f"(attempt {attempt}/{_ALTERLAB_5XX_MAX_ATTEMPTS}); retrying "
                    f"after {_ALTERLAB_5XX_BACKOFF_SECONDS * attempt:.0f}s."
                )
                time.sleep(_ALTERLAB_5XX_BACKOFF_SECONDS * attempt)
                continue

            # ADR-083: a transient `browser_pool_exhausted` 422 is a capacity
            # blip, not a malformed request — retry it (longer backoff) rather
            # than dropping to a fetcher that bot-walled vendors block. Record
            # the flag regardless of whether the retry ultimately succeeds, so
            # the source-reason classifier can name the cause (ADR-084).
            if resp.status_code == 422 and _is_transient_alterlab_422(resp):
                tls.last_alterlab_pool_exhausted = True
                if attempt < _ALTERLAB_5XX_MAX_ATTEMPTS:
                    logger.warning(
                        f"[universal_ai] AlterLab API 422 browser_pool_exhausted "
                        f"(attempt {attempt}/{_ALTERLAB_5XX_MAX_ATTEMPTS}); retrying "
                        f"after {_ALTERLAB_POOL_BACKOFF_SECONDS * attempt:.0f}s."
                    )
                    time.sleep(_ALTERLAB_POOL_BACKOFF_SECONDS * attempt)
                    continue

            # Other 4xx (auth/quota/malformed 422), or a 5xx / exhausted-pool
            # 422 on the final attempt: raise so the caller routes to fallback
            # tiers or bubbles up auth/quota errors.
            resp.raise_for_status()
            payload = resp.json()
            break

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


# --- Retry-on-weak-render with bounded escalation (ADR-071) ----------------
#
# `_fetch_html_with_retry` only retries transient *exceptions*. But hard,
# bot-walled vendors (Target, B&H, Best Buy) routinely answer HTTP 200 with an
# UNUSABLE body: a Cloudflare "Just a moment…" challenge, Target's "There was a
# temporary issue" stub, or an empty/short shell. The system used to trust that
# first 200, so an unlucky fetch silently dropped a vendor (onboarder demoted a
# good detail URL) or zeroed a scheduled run's price. ADR-071 treats a
# weak render as a retryable condition with BOUNDED escalation: re-fetch with
# progressively stronger AlterLab options. Escalation fires ONLY when a render
# is detected weak, so the happy path (most pages succeed on attempt 1) costs
# nothing extra; the worst case is ~3x one fetch for a genuinely flaky vendor.

# Distinctive anti-bot / error-stub phrases. These are specific enough that a
# real product page is very unlikely to contain them, so a whole-body search is
# safe (the Target "temporary issue" stub can be 380 KB, so a size floor alone
# misses it).
_WEAK_RENDER_SIGNATURES = re.compile(
    r"just a moment|checking your browser|attention required|"
    r"please enable (?:js|javascript) and cookies|enable javascript to continue|"
    r"there was a temporary issue|/cdn-cgi/challenge(?!-platform)|captcha-delivery|"
    r"px-captcha|verify you are (?:a )?human|access to this page has been denied",
    re.IGNORECASE,
)

# Below this, an HTTP 200 body is almost certainly a stub/challenge shell rather
# than a rendered storefront. A real rendered product/search page is tens to
# hundreds of KB.
_WEAK_BODY_FLOOR = 2000

_ESCALATION_BACKOFF_SECONDS = 1.0


# --- Per-run circuit breaker + wall-clock budget (ADR-078, R6) -------------
#
# Under a degraded AlterLab a 7-source run took >28 min (2026-05-24 eval): each
# source ground through the full escalation ladder (≈3 × ~60s) plus a curl_cffi
# timeout, even though AlterLab was failing on every one. Once AlterLab is
# clearly down for this run there's no point paying that latency per source.
#
# The breaker is per-RUN module state, reset by ``reset_run_state()`` at the top
# of ``cli._cmd_search`` (mirrors the ``LAST_RUN_USAGE`` reset pattern). It opens
# after ``_BREAKER_THRESHOLD`` consecutive AlterLab-degraded sources; once open,
# ``fetch()`` short-circuits remaining ``universal_ai_search`` sources instead of
# re-running the ladder. A single healthy AlterLab fetch resets the counter, so a
# transient blip doesn't trip it. The wall-clock budget is a second, independent
# guard: once the run has spent ``_RUN_BUDGET_SECONDS`` fetching, remaining
# sources skip regardless of breaker state. Both surface a human-readable reason
# via ``LAST_SKIP_REASON`` that ``cli._cmd_search`` shows in the Sources panel.
_BREAKER_THRESHOLD = 3
# 8 min: tight enough that a stuck tail doesn't push a run past the UI's 20-min
# poll deadline; loose enough for 2-4 healthy sources + AI filter + synth + commit
# (ADR-093, 2026-05-25). Mid-escalation budget check in `_fetch_with_escalation`
# ensures an in-flight source actually honors this.
_RUN_BUDGET_SECONDS = float(os.environ.get("UNIVERSAL_AI_RUN_BUDGET_SECONDS", "480"))

tls = threading.local()

_consecutive_alterlab_failures = 0
_circuit_open = False
_run_deadline: float | None = None
_breaker_lock = threading.Lock()

def reset_run_state() -> None:
    """Reset the per-run circuit breaker + budget. Call once at the top of a
    search run (``cli._cmd_search``) before iterating sources, so state never
    leaks between runs in a long-lived process (or across tests)."""
    global _consecutive_alterlab_failures, _circuit_open, _run_deadline
    with _breaker_lock:
        _consecutive_alterlab_failures = 0
        _circuit_open = False
    _run_deadline = time.monotonic() + _RUN_BUDGET_SECONDS
    tls.last_skip_reason = None
    tls.last_alterlab_pool_exhausted = False
    tls.last_fetch_diagnostics = None


def _budget_exceeded() -> bool:
    return _run_deadline is not None and time.monotonic() >= _run_deadline


def _note_alterlab_outcome(degraded: bool) -> None:
    """Fold one source's AlterLab outcome into the breaker.

    ``degraded`` is True when AlterLab couldn't deliver a usable rendered body
    (5xx-exhausted, fell through to curl_cffi/httpx, or every escalation rung
    was weak). A healthy AlterLab render resets the consecutive counter.
    """
    global _consecutive_alterlab_failures, _circuit_open
    with _breaker_lock:
        if degraded:
            _consecutive_alterlab_failures += 1
            if _consecutive_alterlab_failures >= _BREAKER_THRESHOLD:
                _circuit_open = True
        else:
            _consecutive_alterlab_failures = 0


def _weak_render_reason(html: str, status: int) -> str | None:
    """Return a short reason if the fetched render is unusable, else ``None``.

    Cheap, deterministic, no LLM/parse cost — meant to run on every fetch so
    escalation only fires when something is actually wrong.
    """
    if not html:
        return f"empty body (origin status={status})"
    if status and status >= 400:
        return f"origin HTTP {status}"
    if len(html) < _WEAK_BODY_FLOOR:
        return f"body too short ({len(html)} chars)"
    if _WEAK_RENDER_SIGNATURES.search(html):
        return "anti-bot challenge / error-stub signature in body"
    return None


def _escalation_ladder(
    base: dict[str, Any] | None,
) -> list[dict[str, Any] | None]:
    """Build the bounded escalation ladder of AlterLab option dicts (ADR-071).

    attempt 1: options as given (registry-merged).
    attempt 2: + wait_condition:networkidle (let async-rendered prices settle).
    attempt 3: + min_tier:4 (escalate to the browser tier).
    Duplicate rungs (when the base already carries the option) are skipped so we
    never burn an attempt sending an identical body.

    Tier-4 escalation now goes through the DOCUMENTED ``cost_controls.max_tier``
    body shape (``_fetch_via_alterlab`` maps ``min_tier`` -> ``cost_controls.
    max_tier``), which escalates UP TO tier 4 while returning a fast sync 200
    (Target detail 3/3). This is NOT the legacy top-level ``min_tier:4`` that the
    R2 matrix proved 202-hangs to body 0 — that failure was a property of the
    old flat wire shape, which the documented-shape migration removed. See
    docs/ALTERLAB_OPTIONS.md + ADR-071.
    """
    rungs: list[dict[str, Any] | None] = [base]
    step2 = dict(base or {})
    if step2.get("wait_condition") != "networkidle":
        step2["wait_condition"] = "networkidle"
        step2.setdefault("render_js", True)
        rungs.append(step2)
    step3 = dict(rungs[-1] or {})
    if step3.get("min_tier") != 4:
        step3["min_tier"] = 4
        rungs.append(step3)
    return rungs


def _fetch_with_escalation(
    url: str,
    alterlab_options: dict[str, Any] | None,
) -> tuple[str, int, str, list[str], bool]:
    """Fetch ``url``, escalating AlterLab options on a detected weak render.

    Returns ``(html, status, fetcher, attempts_log, alterlab_degraded)``. When
    AlterLab is not in use (no API key) escalation is a no-op — the stronger
    options only mean something to the rendered path — so we fall straight
    through to the single transient-retry fetch and keep current behaviour
    (``alterlab_degraded`` is always False, the breaker is inert without a key).

    ``alterlab_degraded`` (ADR-078, R6) is True when AlterLab couldn't deliver a
    usable rendered body: every escalation rung was weak, OR the fetch fell
    through to curl_cffi/httpx (a usable body from a cheaper tier still means
    AlterLab failed, so the breaker should count it and stop paying the long
    AlterLab timeout on subsequent sources).
    """
    attempts: list[str] = []
    have_alterlab = bool(os.environ.get("ALTERLAB_API_KEY", "").strip())
    skip_alterlab = alterlab_options and alterlab_options.get("skip_alterlab")
    if not have_alterlab or skip_alterlab:
        html, status, fetcher = _fetch_html_with_retry(url, alterlab_options=alterlab_options)
        return html, status, fetcher, attempts, False

    ladder = _escalation_ladder(alterlab_options)
    best: tuple[str, int, str] | None = None
    for i, opts in enumerate(ladder, start=1):
        # ADR-093: defense-in-depth on the per-run wall-clock budget. Without
        # this, a single in-flight source can blow through the budget paying
        # another full 120s AlterLab timeout per remaining rung; the source-entry
        # check in `fetch()` only protects subsequent sources. Bail after the
        # current rung if the budget has tripped — `best` still holds the
        # strongest weak body so callers don't lose a partial render.
        if i > 1 and _budget_exceeded():
            attempts.append(f"attempt {i}: SKIPPED (per-run budget exceeded)")
            logger.warning(
                f"[universal_ai] per-run budget exceeded mid-escalation for {url}; "
                f"bailing after {i-1} rung(s)"
            )
            break
        if i > 1:
            time.sleep(_ESCALATION_BACKOFF_SECONDS)
        # Exceptions propagate immediately: `_fetch_html_with_retry` already
        # did the ADR-053 transient-retry, and auth/quota / parse errors are not
        # things a stronger render tier can fix. Escalation is ONLY for a weak
        # render (HTTP 200 with an unusable body), never for a raised error.
        html, status, fetcher = _fetch_html_with_retry(url, alterlab_options=opts)
        weak = _weak_render_reason(html, status)
        attempts.append(
            f"attempt {i} (min_tier={opts.get('min_tier') if opts else None},"
            f"wait_condition={opts.get('wait_condition') if opts else None}): "
            f"status={status} len={len(html)} "
            f"{'WEAK: ' + weak if weak else 'OK'}"
        )
        if not weak:
            if i > 1:
                logger.info(f"[universal_ai] escalation recovered {url} on attempt {i}: {attempts[-1]}")
            # A usable body still counts AlterLab as degraded if it only arrived
            # via the curl_cffi/httpx fallback (AlterLab itself failed).
            return html, status, fetcher, attempts, fetcher != "alterlab"
        # Keep the largest body seen as the fallback if every rung is weak.
        if best is None or len(html) > len(best[0]):
            best = (html, status, fetcher)
        logger.warning(f"[universal_ai] weak render for {url}: {attempts[-1]}")

    html, status, fetcher = best if best else ("", 0, "alterlab")
    logger.warning(
        f"[universal_ai] all {len(ladder)} fetch attempts weak for {url}; "
        f"returning best-effort body (len={len(html)})."
    )

    # Dynamic Scrappey Fallback for Weak Renders
    scrappey_key = os.environ.get("SCRAPPEY_API_KEY", "").strip()
    use_scrappey = alterlab_options and alterlab_options.get("use_scrappey")
    if scrappey_key and not use_scrappey:
        logger.warning(
            f"[universal_ai] AlterLab hit weak render/bot wall for {url}; "
            f"dynamically falling back to Scrappey."
        )
        try:
            proxy_country = (alterlab_options or {}).get("proxy_country", "UnitedStates")
            s_html, s_status, s_fetcher = _fetch_via_scrappey(
                url,
                scrappey_key,
                proxy_country,
                triggered_by="dynamic_weak_render_fallback",
                render_js=True,
            )
            attempts.append(f"dynamic_scrappey_fallback: status={s_status} len={len(s_html)}")
            return s_html, s_status, s_fetcher, attempts, False
        except Exception as sc_exc:
            logger.warning(
                f"[universal_ai] Dynamic Scrappey fallback failed "
                f"({type(sc_exc).__name__}: {sc_exc})"
            )
            attempts.append(f"dynamic_scrappey_fallback: FAILED ({sc_exc})")

    return html, status, fetcher, attempts, True


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

# Accessory-bundle marker: a title saying "Bundle" without an explicit
# homogeneous-multi-pack pattern almost always means "product + different
# accessory" (e.g. "WH-1000XM5 ... + Wood Headphone Stand Bundle"), NOT
# 2-of-the-same-item. Sony-wh-1000xm5 Best Buy bundles (2026-05-20) were
# reported at half-price ($269.99 -> $135) because the LLM classified the
# bundle as pack_size=2 and ``_parse_pack`` divided. Used as a guard to
# downgrade LLM-claimed pack_size > 1 back to 1 when only an accessory
# bundle marker is present.
_ACCESSORY_BUNDLE_MARKER = re.compile(r"\bbundle\b", re.IGNORECASE)


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
    # Guard: an LLM-claimed pack_size > 1 is overridden back to 1 when the
    # title contains an accessory-bundle marker ("bundle") but NO explicit
    # numeric multi-pack pattern. See _ACCESSORY_BUNDLE_MARKER above.
    if (
        count > 1
        and _ACCESSORY_BUNDLE_MARKER.search(title)
        and not _PACK_PATTERNS.search(title)
    ):
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

# Per-chunk char budget for the recall-first full-HTML search extractor
# (ADR-077). A search/category page lists many products, so its stripped text
# is far larger than a single detail page — but it's still bounded. We feed the
# whole page to Haiku (cost is not the constraint; recall is) in chunks no
# larger than this. Most rendered search pages strip to well under one chunk
# (Target "bose headphones" → ~11k chars), so chunking only fires on the rare
# huge SPA page. Picked > _DETAIL_MAX_CHARS so search recall is never throttled
# by the detail tier's tighter cap.
_SEARCH_MAX_CHARS = 80000

# Overlap between adjacent chunks so a product whose title/price straddle a
# chunk boundary still appears intact in at least one chunk.
_SEARCH_CHUNK_OVERLAP = 400


def _strip_to_main_text(html: str, max_chars: int | None = _DETAIL_MAX_CHARS) -> str:
    """Reduce a product page to its visible main-content text.

    Drops ``<script>/<style>/<nav>/<header>/<footer>`` etc., flattens the
    remaining body to whitespace-collapsed text, canonicalises Amazon-style
    split price spans, and strips foreign-currency amounts (AlterLab's
    European exit IPs leak EUR/GBP into the DOM). The result is BOTH the
    LLM payload AND the haystack the price-verbatim guard checks against,
    so the guard can never disagree with what the model saw.

    ``max_chars`` caps the returned text (default ``_DETAIL_MAX_CHARS`` for
    the single-product detail tier). Pass ``None`` to get the untruncated
    text — the search-step extractor (ADR-077) needs the whole page and
    chunks it itself against the larger ``_SEARCH_MAX_CHARS`` budget.
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
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars]
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

    _accumulate_usage(resp)

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


# --- Tier 2.5: recall-first full-HTML search extractor (ADR-077) -----------


def _canonical_url(url: str) -> str:
    """``scheme://host/path`` (host lowercased, trailing slash stripped).

    The dedupe key shared by the JSON-LD, anchor-walker and full-HTML tiers so
    the search union (ADR-077) never double-counts the same product reached
    through two extractors.
    """
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"


def _jsonld_to_listings(
    jsonld_listings: list[dict[str, Any]],
    fetched_at: datetime,
    parsed_host: str,
    extractor: str = "jsonld",
) -> list[Listing]:
    """Map ``{title,url,price_usd,condition}`` dicts to ``Listing`` rows.

    Shared by the JSON-LD tier and the embedded-state recovery tier (ADR-106),
    which produce the same dict shape deterministically; ``extractor`` tags the
    provenance so the union log / debugging can tell them apart.
    """
    results: list[Listing] = []
    for item in jsonld_listings:
        is_kit, kit_module_count, unit_price_usd, kit_price_usd = _parse_pack(
            item["title"], item["price_usd"]
        )
        results.append(
            Listing(
                source="universal_ai_search",
                url=item["url"],
                title=item["title"],
                fetched_at=fetched_at,
                brand=None,
                mpn=None,
                attrs={"vendor_host": parsed_host, "extractor": extractor},
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
    return results


# --- Embedded search-state recovery tier (ADR-106) -------------------------
#
# Some big retailers (Walmart) render their search grid entirely from a JSON
# blob the server embeds in a <script> (Next.js ``__NEXT_DATA__``). The visible
# DOM the LLM tiers read is built from it client-side, so on a thin/partial
# render the stripped text carries no products and BOTH the anchor walker and
# the full-HTML LLM tier emit 0 — the "substantive body, 0 parsed" parser-gap
# the 2026-05-27 DJI run hit on Walmart. The product data is sitting in the
# embedded state the whole time; reading it deterministically (no LLM, no extra
# fetch) recovers the grid regardless of how much of the DOM rendered. URLs and
# prices are copied verbatim from the structured blob — the LLM never types
# either — so the no-fabrication boundary (ADR-001) is preserved exactly as in
# the JSON-LD tier.


def _embedded_money(value: Any) -> float | None:
    """Coerce an embedded-state price (numeric or ``"$1,299.00"``) to a float.

    Unlike ``_coerce_price`` this keeps thousands separators correct: a US price
    string like ``"$3,299.95"`` must parse to ``3299.95`` (``_coerce_price``'s
    European-comma heuristic mangles it to ``3.299``).
    """
    if isinstance(value, (int, float)):
        f = float(value)
        return f if f > 0 else None
    if isinstance(value, str):
        m = re.search(r"\d[\d,]*(?:\.\d+)?", value)
        if not m:
            return None
        try:
            f = float(m.group(0).replace(",", ""))
        except ValueError:
            return None
        return f if f > 0 else None
    return None


def _iter_walmart_item_stacks(node: Any) -> Any:
    """Yield every ``itemStacks`` list (of stack dicts) reachable from ``node``.

    Recurses without assuming the exact parent path
    (``props.pageProps.initialData.searchResult.itemStacks``) so a Next.js
    layout reshuffle doesn't silently break recovery.
    """
    if isinstance(node, dict):
        stacks = node.get("itemStacks")
        if isinstance(stacks, list) and any(
            isinstance(s, dict) and isinstance(s.get("items"), list) for s in stacks
        ):
            yield stacks
        for v in node.values():
            yield from _iter_walmart_item_stacks(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_walmart_item_stacks(v)


def _extract_embedded_state_listings(
    html: str, base_url: str
) -> list[dict[str, Any]]:
    """Extract ``{title,url,price_usd,condition}`` dicts from embedded JSON state.

    Currently handles Walmart's Next.js ``__NEXT_DATA__`` search grid
    (``searchResult.itemStacks[].items[]``). Returns the same dict shape as
    ``_extract_jsonld_listings`` so it flows through ``_jsonld_to_listings``.
    Empty list when no recognised embedded grid is present (the no-op case for
    every non-Walmart vendor).
    """
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S
    )
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return []

    listings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stacks in _iter_walmart_item_stacks(data):
        for stack in stacks:
            if not isinstance(stack, dict):
                continue
            for item in stack.get("items", []):
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                href = item.get("canonicalUrl") or item.get("productPageUrl")
                if not isinstance(href, str) or not href.strip():
                    continue
                url_abs = urljoin(base_url, href.strip())
                parsed = urlparse(url_abs)
                if parsed.scheme not in ("http", "https"):
                    continue
                canonical = (
                    f"{parsed.scheme}://{parsed.netloc.lower()}"
                    f"{parsed.path.rstrip('/')}"
                )
                if canonical in seen:
                    continue

                price_info = item.get("priceInfo")
                price = _embedded_money(item.get("price"))
                if price is None and isinstance(price_info, dict):
                    price = _embedded_money(price_info.get("linePrice"))
                    if price is None:
                        current = price_info.get("currentPrice")
                        if isinstance(current, dict):
                            price = _embedded_money(current.get("price"))
                if price is None:
                    continue

                condition = "used" if item.get("isPreowned") else "new"
                seen.add(canonical)
                listings.append({
                    "title": name.strip()[:300],
                    "url": url_abs,
                    "price_usd": price,
                    "condition": condition,
                })
    return listings


def _extract_via_embedded_state(
    html: str,
    url: str,
    *,
    fetched_at: datetime,
    parsed_host: str,
) -> list[Listing]:
    """Tier 0.5 (ADR-106): recover products from embedded JSON search state.

    Deterministic, free, no LLM. Runs alongside the JSON-LD / anchor / full-HTML
    tiers and is UNIONed first (it is the most authoritative source when present
    — the server's own product model), closing the substantive-body-but-0-parsed
    parser gap on embedded-state retailers like Walmart.
    """
    embedded = _extract_embedded_state_listings(html, base_url=url)
    if embedded:
        logger.info(
            f"[universal_ai] Embedded-state tier recovered {len(embedded)} "
            f"listing(s) from {url}."
        )
    return _jsonld_to_listings(
        embedded, fetched_at, parsed_host, extractor="embedded_state"
    )


def _union_by_canonical(*listing_groups: list[Listing]) -> list[Listing]:
    """Merge listing groups, keeping the FIRST listing seen per canonical URL.

    Order matters: callers pass the most-structured tier first (embedded state,
    JSON-LD, then the anchor walker, then the full-HTML LLM) so the
    higher-quality extractor wins a tie and the looser tiers only ever ADD
    products the others missed — the additive-recall guarantee of ADR-077/106.
    """
    seen: set[str] = set()
    merged: list[Listing] = []
    for group in listing_groups:
        for listing in group:
            canonical = _canonical_url(listing.url)
            if canonical in seen:
                continue
            seen.add(canonical)
            merged.append(listing)
    return merged


def _collect_search_anchors(html: str, base_url: str) -> list[dict[str, Any]]:
    """Every ``<a href>`` on the page, deduped by canonical URL, UNFILTERED.

    Unlike ``_extract_candidates`` this applies NO nav-path / UI-chrome /
    price-proximity / cap-80 filtering — the whole point of ADR-077 is that
    those heuristics are the recall ceiling. The LLM picks a product's link by
    ``idx``; ``idx`` equals the list position so the caller can index back
    safely, and the URL is therefore never typed by the LLM (no hallucination).
    """
    try:
        from selectolax.parser import HTMLParser
    except ImportError:
        logger.error("selectolax is required for universal_ai extraction.")
        return []

    tree = HTMLParser(html)
    if tree.body is None:
        return []

    anchors: list[dict[str, Any]] = []
    idx_by_canonical: dict[str, int] = {}
    for a in tree.css("a"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(_SKIP_HREF_PREFIXES):
            continue
        href_abs = urljoin(base_url, href)
        parsed = urlparse(href_abs)
        if parsed.scheme not in ("http", "https"):
            continue
        canonical = (
            f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
        )
        title = _anchor_title(a)
        if canonical in idx_by_canonical:
            existing = anchors[idx_by_canonical[canonical]]
            if len(title) > len(existing["title"]):
                existing["title"] = title[:240]
            continue
        idx_by_canonical[canonical] = len(anchors)
        anchors.append({
            "idx": len(anchors),
            "title": title[:240],
            "href_abs": href_abs,
            "canonical": canonical,
        })
    return anchors


def _chunk_text(
    text: str, max_chars: int, overlap: int = _SEARCH_CHUNK_OVERLAP
) -> list[str]:
    """Split ``text`` into ``<= max_chars`` chunks with a small overlap so a
    product whose title+price straddle a boundary survives intact in one chunk.
    """
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        chunks.append(text[start:end])
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _search_spec_attr_prompt(prompt: str, profile: Any | None) -> tuple[str, list[str]]:
    """Append profile ``spec_attrs`` extraction bullets to the search prompt.

    Mirrors the anchor tier so spec attributes (e.g. capacity, screen size)
    are extracted by the full-HTML tier too. Returns ``(prompt, extra_keys)``.
    """
    extra_keys: list[str] = []
    if not (profile and hasattr(profile, "spec_attrs") and profile.spec_attrs):
        return prompt, extra_keys
    extra_lines: list[str] = []
    for k, attr_def in profile.spec_attrs.items():
        extra_keys.append(k)
        attr_type = getattr(attr_def, "type", "str")
        desc = (
            f'  - "{k}": extract this attribute from the title or PAGE TEXT if '
            f"present (type: {attr_type})."
        )
        if hasattr(attr_def, "enum") and attr_def.enum:
            desc += f" Must be one of: {attr_def.enum}."
        extra_lines.append(desc)
    if extra_lines:
        prompt = prompt.replace(
            '  - "link_idx":',
            "\n".join(extra_lines) + '\n  - "link_idx":',
        )
    return prompt, extra_keys


def _extract_via_full_html(
    html: str,
    url: str,
    *,
    profile: Any | None,
    fetched_at: datetime,
    parsed_host: str,
) -> list[Listing]:
    """Tier 2.5 (ADR-077): enumerate ALL products from the whole rendered page.

    Hands Haiku the full stripped page text (chunked at ``_SEARCH_MAX_CHARS``)
    plus a numbered list of every link, and asks it to enumerate products and
    map each to a link by index. Every emitted price is re-verified verbatim
    against the fetched text (anti-fabrication, ADR-001); the URL is the
    indexed anchor's href, never typed by the LLM. Returns ``[]`` on any failure
    so the union still has the JSON-LD + anchor-walker tiers.
    """
    full_text = _strip_to_main_text(html, max_chars=None)
    if len(full_text) < 20:
        return []
    anchors = _collect_search_anchors(html, base_url=url)
    if not anchors:
        return []
    links_block = "\n".join(
        f'{a["idx"]}: {a["title"]}' for a in anchors if a["title"]
    )
    if not links_block:
        return []

    prompt, extra_keys = _search_spec_attr_prompt(
        SEARCH_FULL_HTML_SYSTEM_PROMPT, profile
    )

    results: list[Listing] = []
    seen_canonical: set[str] = set()
    for chunk in _chunk_text(full_text, _SEARCH_MAX_CHARS):
        user_content = f"PAGE TEXT:\n{chunk}\n\nLINKS:\n{links_block}"
        try:
            resp = call_llm(
                provider="anthropic",
                model="claude-haiku-4-5",
                system=prompt,
                messages=[Message(role="user", content=user_content)],
                response_format="json",
                max_tokens=4096,
            )
        except Exception as exc:
            logger.error(
                f"[universal_ai] Tier 2.5 full-HTML LLM call failed: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        _accumulate_usage(resp)

        parsed = _extract_json(resp.text or "")
        if isinstance(parsed, dict) and isinstance(parsed.get("products"), list):
            products = parsed["products"]
        elif isinstance(parsed, list):
            products = parsed
        else:
            logger.warning(
                f"[universal_ai] Tier 2.5: unexpected JSON shape for {url}: "
                f"{str(parsed)[:200]}"
            )
            continue

        for p in products:
            if not isinstance(p, dict):
                continue
            try:
                link_idx = int(p["link_idx"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= link_idx < len(anchors)):
                continue
            anchor = anchors[link_idx]

            raw_price = p.get("price_usd")
            if raw_price is None:
                continue
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue

            # Anti-fabrication guard (ADR-001): the price MUST occur verbatim
            # in the fetched text. The URL can't be fabricated (it's the
            # indexed anchor), but the price is LLM-spoken, so verify it.
            if not _price_in_text(price, full_text):
                logger.warning(
                    f"[universal_ai] Tier 2.5: price {price} for "
                    f"{anchor['href_abs']} NOT found verbatim in fetched body; "
                    f"dropping (anti-hallucination guard, ADR-001)."
                )
                continue

            title = (p.get("title") or anchor["title"] or "").strip()
            if not title:
                continue

            canonical = anchor["canonical"]
            if canonical in seen_canonical:
                continue
            seen_canonical.add(canonical)

            condition = str(p.get("condition") or "new").strip().lower()
            if condition not in ("new", "used", "refurbished"):
                condition = "new"

            attrs: dict[str, Any] = {
                "vendor_host": parsed_host,
                "extractor": "full_html_llm",
            }
            for k in extra_keys:
                if k in p and p[k] is not None:
                    attrs[k] = p[k]

            try:
                llm_pack_size = int(p.get("pack_size") or 1)
            except (TypeError, ValueError):
                llm_pack_size = 1
            is_kit, kit_module_count, unit_price_usd, kit_price_usd = _parse_pack(
                title, price, llm_pack_size=llm_pack_size
            )

            results.append(Listing(
                source="universal_ai_search",
                url=anchor["href_abs"],
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

    logger.info(
        f"[universal_ai] Tier 2.5 full-HTML extracted {len(results)} listing(s) "
        f"from {url} (anchors={len(anchors)})."
    )
    return results


def _extract_via_anchor_walker(
    html: str,
    url: str,
    *,
    profile: Any | None,
    fetched_at: datetime,
    parsed_host: str,
) -> list[Listing]:
    """Tier 2: the anchor-walker candidate set structured by Haiku.

    Unchanged behaviour, factored out of ``fetch`` so the search union can call
    it alongside the JSON-LD and full-HTML tiers. Returns ``[]`` when the walker
    finds no candidates or the LLM emits nothing usable.
    """
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
    extra_keys: list[str] = []
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

    _accumulate_usage(resp)

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

    logger.info(f"[universal_ai] Anchor tier emitted {len(results)} listings from {url}.")
    return results


def _degrade_search_url(url: str) -> str | None:
    """Attempt to degrade search keywords in ``url`` by dropping the last word.

    Returns the degraded URL, or None if no degradation is possible.
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)

    # Common search query parameters
    search_keys = ["q", "st", "query", "keywords", "k", "keyword", "searchTerm", "search_query"]
    found_key = None
    original_val = None

    for key in search_keys:
        if key in query_params:
            found_key = key
            original_val = query_params[key][0]
            break

    if found_key is not None and original_val is not None:
        normal_str = unquote_plus(original_val)
        words = [w for w in re.split(r'[\s+]+', normal_str) if w]
        if len(words) > 1:
            degraded_str = " ".join(words[:-1])
            new_params = dict(query_params)
            new_params[found_key] = [degraded_str]
            new_query = urlencode(new_params, doseq=True)
            new_parts = list(parsed)
            new_parts[4] = new_query
            return urlunparse(new_parts)

    # If not in query params, try path segment (e.g. /s/dyson+v15+detect or /search/dyson+v15+detect)
    path_segments = parsed.path.split('/')
    found_path_idx = None
    for i, segment in enumerate(path_segments):
        if segment in ("s", "search") and i + 1 < len(path_segments) and path_segments[i + 1]:
            found_path_idx = i + 1
            original_val = path_segments[i + 1]
            break

    if found_path_idx is not None and original_val is not None:
        normal_str = unquote_plus(original_val)
        words = [w for w in re.split(r'[\s+]+', normal_str) if w]
        if len(words) > 1:
            degraded_str = " ".join(words[:-1])
            new_segments = list(path_segments)
            new_segments[found_path_idx] = quote_plus(degraded_str)
            new_path = "/".join(new_segments)
            new_parts = list(parsed)
            new_parts[2] = new_path
            return urlunparse(new_parts)

    return None


# --- Main entry point ------------------------------------------------------


# --- Carry-gate (ADR-099) --------------------------------------------------
#
# Before spending Haiku tokens on the anchor + full-HTML extractors for a
# SEARCH page, check whether the product is actually present on the page. A
# vendor the user keeps in the profile "in case it stocks the item later" but
# that doesn't carry it today otherwise costs a full LLM extraction every run
# to surface guaranteed-junk. The gate is purely deterministic and only ever
# *suppresses* a paid call — it never produces data (ADR-001 intact).


def _normalize_alnum(text: str) -> str:
    """Lowercase and drop every non-alphanumeric char.

    Lets aliases (and the page) be compared separator-insensitively, so
    ``MBD-H14SSL-N-O``, ``H14SSL N`` and ``h14ssln`` all normalize to a common
    contiguous form.
    """
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _model_family_token(display_name: str) -> str | None:
    """Derive a recall-safe family-core token from a product ``display_name``.

    Rule: take the longest whitespace word containing BOTH a letter and a digit
    (a model-number shape, e.g. ``H14SSL-N``); reduce it to the first
    hyphen/slash segment that still contains a digit (``H14SSL``); normalize to
    lowercase-alphanumeric (``h14ssl``). "Family core" (ADR-099, user's
    recall-safe choice) means the gate wakes on ``-NT`` / ``MBD-…-O`` variants
    too — the relevance filter then sorts them.

    Returns ``None`` when no confident model token exists (normalized core < 5
    chars, e.g. ``The Economist 1yr subscription`` → only digit-word ``1yr``),
    so the gate self-disables and extraction runs as before.
    """
    words = re.split(r"\s+", display_name.strip())
    candidates = [
        w for w in words
        if re.search(r"[a-zA-Z]", w) and re.search(r"\d", w)
    ]
    if not candidates:
        return None
    model_word = max(candidates, key=len)
    segments = re.split(r"[-/]", model_word)
    core = next((s for s in segments if re.search(r"\d", s)), model_word)
    norm = _normalize_alnum(core)
    return norm if len(norm) >= 5 else None


def _carry_gate_terms(profile: Any | None) -> tuple[str | None, list[str]]:
    """Return ``(family_core_token, normalized_aliases)`` for the carry-gate.

    ``family_core_token`` is ``None`` when the gate should be disabled (no
    confident model token in ``display_name``).
    """
    if profile is None:
        return None, []
    token = _model_family_token(getattr(profile, "display_name", "") or "")
    aliases_raw = getattr(profile, "match_aliases", None) or []
    aliases = [
        norm for a in aliases_raw
        if isinstance(a, str) and (norm := _normalize_alnum(a))
    ]
    return token, aliases


def _page_carries_product(
    html: str, family_core: str, aliases: list[str]
) -> bool:
    """True if the family-core token OR any normalized alias is on the page.

    The family core is a separator-free alphanumeric run, so it is matched in
    the lowercased HTML directly (``h14ssl`` is a substring of ``h14ssl-n``).
    Aliases are matched against the alphanumeric-normalized page so spacing and
    punctuation don't matter.
    """
    if family_core and family_core in html.lower():
        return True
    if aliases:
        page_norm = _normalize_alnum(html)
        if any(alias in page_norm for alias in aliases):
            return True
    return False


def fetch(query: AdapterQuery, profile: Any | None = None) -> list[Listing]:
    """Fetch and extract product listings from an arbitrary vendor URL."""
    tls.last_run_usage = None
    tls.last_skip_reason = None
    tls.last_fetch_diagnostics = None
    tls.last_alterlab_pool_exhausted = False
    tls.scrappey_diagnostics = []

    if os.environ.get("WORKER_USE_FIXTURES", "").strip() in ("1", "true", "yes"):
        logger.info("WORKER_USE_FIXTURES=1; universal_ai returning empty list.")
        return []

    url = query.extra.get("url") or query.storefront_url
    if not url:
        logger.warning("No 'url' in profile source for universal_ai_search.")
        return []

    # ADR-078 (R6): short-circuit when AlterLab is clearly down for this run or
    # the per-run fetch budget is spent — don't grind this source through the
    # full escalation ladder + curl_cffi timeout just to fail like the last N.
    if _circuit_open:
        tls.last_skip_reason = (
            f"skipped: AlterLab circuit open after "
            f"{_consecutive_alterlab_failures} consecutive failures"
        )
        logger.warning(f"[universal_ai] {tls.last_skip_reason}; not fetching {url}.")
        return []
    if _budget_exceeded():
        tls.last_skip_reason = (
            f"skipped: per-run fetch budget ({_RUN_BUDGET_SECONDS:.0f}s) exceeded"
        )
        logger.warning(f"[universal_ai] {tls.last_skip_reason}; not fetching {url}.")
        return []

    alterlab_options = query.extra.get("alterlab_options")
    nested_extra = query.extra.get("extra")
    if not alterlab_options and isinstance(nested_extra, dict):
        alterlab_options = nested_extra.get("alterlab_options")
    if not isinstance(alterlab_options, dict):
        alterlab_options = None

    # ADR-068: vendor quirks registry. Defaults from product_search/vendor_quirks.yaml
    # are merged UNDER explicit per-source options (source wins on conflict), and
    # registered URL transforms (e.g. Best Buy ?intl=nosplash) are applied before
    # fetch. Source can opt out with extra.skip_vendor_quirks: true — required
    # for the rare case where a profile intentionally needs the raw URL/options.
    skip_quirks = bool(query.extra.get("skip_vendor_quirks"))
    if not skip_quirks and isinstance(nested_extra, dict):
        skip_quirks = bool(nested_extra.get("skip_vendor_quirks"))
    applied_transforms: list[str] = []
    if not skip_quirks:
        merged_quirks = merge_alterlab_options(url, alterlab_options)
        if merged_quirks != alterlab_options:
            logger.info(
                f"[universal_ai] vendor_quirks: merged alterlab_options "
                f"{alterlab_options or {}} <- defaults => {merged_quirks}"
            )
        alterlab_options = merged_quirks
        transformed_url, applied_transforms = apply_url_transforms(url)
        if applied_transforms:
            logger.info(
                f"[universal_ai] vendor_quirks: applied {applied_transforms} -> {transformed_url}"
            )
            url = transformed_url

    logger.info(f"[universal_ai] Fetching {url}")
    try:
        html, status, fetcher, attempts, alterlab_degraded = _fetch_with_escalation(
            url, alterlab_options
        )
    except Exception as exc:
        # A raised fetch (auth/quota, or AlterLab + curl_cffi both failing) is an
        # AlterLab-degraded outcome for breaker purposes (ADR-078, R6).
        _note_alterlab_outcome(degraded=True)
        tls.last_fetch_diagnostics = {
            "body_len": 0,
            "final_status": 0,
            "final_fetcher": None,
            "alterlab_degraded": True,
            "alterlab_pool_exhausted": getattr(tls, "last_alterlab_pool_exhausted", False),
        }
        logger.error(f"[universal_ai] Fetch failed: {type(exc).__name__}: {exc}")
        # Bubble up explicit fetch errors (like AlterLab quota/auth) so cli.py
        # can surface them in the UI.
        raise
    _note_alterlab_outcome(alterlab_degraded)
    tls.last_fetch_diagnostics = {
        "body_len": len(html),
        "final_status": status,
        "final_fetcher": fetcher,
        "alterlab_degraded": alterlab_degraded,
        "alterlab_pool_exhausted": getattr(tls, "last_alterlab_pool_exhausted", False),
    }

    logger.info(
        f"[universal_ai] Fetched via {fetcher}: status={status}, "
        f"body_len={len(html)} chars"
        + (f" [escalated: {len(attempts)} attempts]" if len(attempts) > 1 else "")
    )
    if not html:
        logger.warning(f"[universal_ai] Empty body for {url}.")
        return []

    fetched_at = datetime.now(tz=UTC)
    parsed_host = urlparse(url).netloc.lower()

    # Phase 15 tier 1: JSON-LD. Free, deterministic, no LLM call.
    jsonld_listings = _extract_jsonld_listings(html, base_url=url)
    jsonld_results = _jsonld_to_listings(jsonld_listings, fetched_at, parsed_host)
    if jsonld_results:
        logger.info(
            f"[universal_ai] Extracted {len(jsonld_results)} listing(s) from "
            f"JSON-LD on {url}."
        )

    # Tier 1.5 (ADR-049): single-product detail-page extractor. Runs after
    # JSON-LD found nothing, only for detail-flagged / detail-shaped URLs.
    detail_mode = _resolve_detail_mode(query, url)
    if detail_mode != "search":
        if jsonld_results:
            # Detail page with JSON-LD — return immediately (best-quality).
            return jsonld_results
        detail_listings = _extract_detail_listing(
            html, url,
            profile=profile,
            fetched_at=fetched_at,
            parsed_host=parsed_host,
        )
        if detail_listings:
            return detail_listings
        if detail_mode == "detail":
            # ADR-125: before giving up on an explicit detail source, mirror the
            # ADR-107 known-good thin-body Scrappey fallback that the search path
            # gets at the bottom of this function. The detail branch returns
            # early (below), so without this an alterlab_known_good vendor that
            # bot-walls AlterLab with a thin body — e.g. an Amazon detail URL —
            # never gets the Scrappey retry and silently reports 0/"doesn't
            # carry." Only fires on a thin body (a real full render that just has
            # no price is left alone) for a known-good vendor that didn't already
            # fetch via Scrappey.
            scrappey_key = os.environ.get("SCRAPPEY_API_KEY", "").strip()
            if (
                scrappey_key
                and len(html) < THIN_BODY_CEILING
                and not (alterlab_options and alterlab_options.get("use_scrappey"))
                and get_quirks_for_url(url).get("alterlab_known_good")
            ):
                logger.warning(
                    f"[universal_ai] Detail extraction found nothing and body is "
                    f"thin ({len(html)} bytes) for known-good vendor {url}. "
                    f"Falling back to Scrappey (ADR-125)."
                )
                try:
                    proxy_country = (alterlab_options or {}).get(
                        "proxy_country", "UnitedStates"
                    )
                    s_html, _s_status, s_fetcher = _fetch_via_scrappey(
                        url,
                        scrappey_key,
                        proxy_country,
                        triggered_by="adr125_detail_recovery",
                        render_js=True,
                    )
                    if s_html:
                        s_recovered = _jsonld_to_listings(
                            _extract_jsonld_listings(s_html, base_url=url),
                            fetched_at,
                            parsed_host,
                        ) or _extract_detail_listing(
                            s_html, url,
                            profile=profile,
                            fetched_at=fetched_at,
                            parsed_host=parsed_host,
                        )
                        if s_recovered:
                            logger.info(
                                f"[universal_ai] Scrappey detail recovery found "
                                f"{len(s_recovered)} listing(s) for {url}."
                            )
                            tls.last_fetch_diagnostics["final_fetcher"] = s_fetcher
                            tls.last_fetch_diagnostics["body_len"] = len(s_html)
                            tls.last_fetch_diagnostics["alterlab_degraded"] = True
                            return s_recovered
                except Exception as exc:
                    logger.warning(
                        f"[universal_ai] Scrappey detail recovery failed "
                        f"({type(exc).__name__}: {exc})"
                    )
            # Explicit opt-in: the page IS one product; the anchor tier
            # would only emit nav junk. Don't burn a second LLM call.
            logger.info(
                f"[universal_ai] Tier 1.5 yielded nothing for explicit "
                f"detail source {url}; not falling through to anchor tier."
            )
            return []
        # detail_mode == "auto" (URL-shape heuristic): fall through to the
        # search union so a real search/category page is never regressed.

    # --- Carry-gate (ADR-099) ---------------------------------------------
    # Skip the paid anchor + full-HTML LLM extractors when the product isn't on
    # the page. JSON-LD already ran (free); keep whatever it found. Only gate
    # when a confident family-core model token exists — otherwise extract as
    # before (the gate self-disables for products it can't reason about).
    gate_token, gate_aliases = _carry_gate_terms(profile)
    if gate_token is not None and not _page_carries_product(
        html, gate_token, gate_aliases
    ):
        n = len(gate_aliases)
        tls.last_skip_reason = (
            f"{WATCH_GATE_REASON_PREFIX} product identifier '{gate_token}' "
            f"not present on page (+{n} alias{'es' if n != 1 else ''} checked)"
        )
        logger.info(
            f"[universal_ai] carry-gate: '{gate_token}' absent from {url}; "
            f"skipping paid LLM extraction (WATCHED). "
            f"Keeping {len(jsonld_results)} JSON-LD listing(s)."
        )
        return jsonld_results

    # --- Search union (ADR-077) -------------------------------------------
    #
    # Run the anchor-walker AND the full-HTML extractor in parallel (they
    # are independent), then UNION their results with JSON-LD (if any).
    # Dedupe by canonical URL, first-seen wins — so JSON-LD > anchor >
    # full-HTML in priority, and the full-HTML tier only ever ADDS products
    # the structured tiers missed.
    embedded_results = _extract_via_embedded_state(
        html, url,
        fetched_at=fetched_at,
        parsed_host=parsed_host,
    )
    anchor_results = _extract_via_anchor_walker(
        html, url,
        profile=profile,
        fetched_at=fetched_at,
        parsed_host=parsed_host,
    )
    full_html_results = _extract_via_full_html(
        html, url,
        profile=profile,
        fetched_at=fetched_at,
        parsed_host=parsed_host,
    )

    merged: list[Listing] = _union_by_canonical(
        embedded_results, jsonld_results, anchor_results, full_html_results,
    )

    # ADR-107: Generalize automatic Scrappey fallback to known-good thin-body vendors.
    scrappey_key = os.environ.get("SCRAPPEY_API_KEY", "").strip()
    if not merged and scrappey_key and len(html) < THIN_BODY_CEILING:
        quirks = get_quirks_for_url(url)
        # If it's alterlab_known_good but didn't already use Scrappey (to guard against double-fetch)
        if quirks.get("alterlab_known_good") and not (alterlab_options and alterlab_options.get("use_scrappey")):
            logger.warning(
                f"[universal_ai] Primary search yielded 0 listings and body is thin "
                f"({len(html)} bytes) for known-good vendor {url}. "
                f"Dynamically falling back to Scrappey (ADR-107)."
            )
            try:
                proxy_country = (alterlab_options or {}).get("proxy_country", "UnitedStates")
                s_html, s_status, s_fetcher = _fetch_via_scrappey(
                    url,
                    scrappey_key,
                    proxy_country,
                    triggered_by="adr107_post_extract",
                    render_js=True,
                )
                if s_html:
                    s_jsonld_listings = _extract_jsonld_listings(s_html, base_url=url)
                    s_jsonld_results = _jsonld_to_listings(s_jsonld_listings, fetched_at, parsed_host)
                    s_embedded_results = _extract_via_embedded_state(
                        s_html, url,
                        fetched_at=fetched_at,
                        parsed_host=parsed_host,
                    )
                    s_anchor_results = _extract_via_anchor_walker(
                        s_html, url,
                        profile=profile,
                        fetched_at=fetched_at,
                        parsed_host=parsed_host,
                    )
                    s_full_html_results = _extract_via_full_html(
                        s_html, url,
                        profile=profile,
                        fetched_at=fetched_at,
                        parsed_host=parsed_host,
                    )
                    
                    s_merged = _union_by_canonical(
                        s_embedded_results, s_jsonld_results,
                        s_anchor_results, s_full_html_results,
                    )
                    if s_merged:
                        logger.info(
                            f"[universal_ai] Scrappey fallback successfully recovered {len(s_merged)} listing(s)!"
                        )
                        merged = s_merged
                        tls.last_fetch_diagnostics["final_fetcher"] = s_fetcher
                        tls.last_fetch_diagnostics["body_len"] = len(s_html)
                        tls.last_fetch_diagnostics["alterlab_degraded"] = True
            except Exception as exc:
                logger.warning(
                    f"[universal_ai] Scrappey fallback failed: {type(exc).__name__}: {exc}"
                )

    # Keyword degradation fallback if merged has 0 listings
    if not merged:
        degraded_url = _degrade_search_url(url)
        if degraded_url:
            logger.info(
                f"[universal_ai] Primary search yielded 0 listings. Attempting search-term "
                f"keyword degradation fallback: {url} -> {degraded_url}"
            )
            try:
                # Secondary fetch of the same vendor; the breaker already noted
                # the primary outcome, so discard this rung's degraded flag.
                d_html, d_status, d_fetcher, d_attempts, _d_degraded = _fetch_with_escalation(
                    degraded_url, alterlab_options
                )
            except Exception as exc:
                logger.warning(
                    f"[universal_ai] Degraded search fetch failed: {type(exc).__name__}: {exc}"
                )
                d_html = ""

            if d_html:
                logger.info(
                    f"[universal_ai] Degraded search page fetched via {d_fetcher}: "
                    f"status={d_status}, body_len={len(d_html)}"
                )
                d_jsonld_listings = _extract_jsonld_listings(d_html, base_url=degraded_url)
                d_jsonld_results = _jsonld_to_listings(d_jsonld_listings, fetched_at, parsed_host)
                d_embedded_results = _extract_via_embedded_state(
                    d_html, degraded_url,
                    fetched_at=fetched_at,
                    parsed_host=parsed_host,
                )
                d_anchor_results = _extract_via_anchor_walker(
                    d_html, degraded_url,
                    profile=profile,
                    fetched_at=fetched_at,
                    parsed_host=parsed_host,
                )
                d_full_html_results = _extract_via_full_html(
                    d_html, degraded_url,
                    profile=profile,
                    fetched_at=fetched_at,
                    parsed_host=parsed_host,
                )
                d_merged = _union_by_canonical(
                    d_embedded_results, d_jsonld_results,
                    d_anchor_results, d_full_html_results,
                )
                if d_merged:
                    logger.info(
                        f"[universal_ai] Degraded search successfully recovered {len(d_merged)} listing(s)!"
                    )
                    merged = d_merged

    full_html_unique = sum(
        1 for listing in merged
        if listing.attrs.get("extractor") == "full_html_llm"
    )
    logger.info(
        f"[universal_ai] Search union: embedded={len(embedded_results)} "
        f"jsonld={len(jsonld_results)} anchor={len(anchor_results)} "
        f"full_html={len(full_html_results)} merged={len(merged)} "
        f"(full_html added {full_html_unique} unique)."
    )
    return merged


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
