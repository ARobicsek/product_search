import 'server-only';

import Anthropic from '@anthropic-ai/sdk';

// TypeScript port of the worker's universal_ai probe (Phase 15 task 5).
//
// Why a TS port and not a subprocess to the Python CLI: the onboarder runs
// in the Vercel Node runtime, where forking subprocesses is unavailable on
// the edge tier and slow on the Node tier. Re-implementing the probe in TS
// keeps the save-time gate fast (parallel fetches, no Python boot) and
// avoids adding a worker-deployment dependency to the web app.
//
// What we DON'T port: the search-page anchor + LLM tier from
// worker/src/product_search/adapters/universal_ai.py — for a SEARCH URL the
// probe only counts JSON-LD listings + product anchors as a coarse signal.
//
// What we DO port (added 2026-05-21): the Tier 1.5 detail-page extractor
// (_extract_detail_listing) is faithfully mirrored here for page_type:"detail"
// URLs. A detail page legitimately has ~0 list anchors and often no JSON-LD —
// its price lives in DOM text — so gating a detail URL on anchorCount is a
// false negative that wrongly demotes a perfectly-good vendor (the B&H bug,
// 2026-05-21). For a detail URL we instead report a `detailExtractable`
// signal: strip the page to its main text, run ONE claude-haiku-4-5 call for
// the single product's price, and re-verify that price verbatim in the
// stripped text (ADR-001 anti-hallucination guard) — exactly what the runtime
// adapter does. anchorCount is no longer the extractability signal for detail
// URLs; the onboarder prompt is told to judge by detailExtractable instead.
//
// Probe pass/fail policy (post-2026-05-04 revision): the gate is a
// HARD-FAILURE-ONLY filter, not a correctness gate. The first pass of this
// probe demoted backmarket — the one universal_ai vendor we knew worked in
// production — because the TS-side raw ``fetch`` got the same Cloudflare
// challenge the Python ``httpx`` path gets, and we then concluded "no
// JSON-LD, no anchors". But the production worker uses AlterLab, which DOES
// render backmarket. So a URL that looks dead to ``fetch`` may be very much
// alive in production. The probe now demotes only:
//   - network errors (timeout, DNS fail)
//   - hard 4xx (404 / 410 — URL doesn't exist anywhere)
//   - sub-500-byte bodies (clearly an empty / error page, not a real site)
// "Cloudflare-challenge-shaped 200 with no products visible" is no longer
// a demotion signal. The reported jsonldCount / anchorCount fields are
// kept on the result for diagnostics but don't influence ok=true/false.
//
// Mirrors:
//   - _extract_jsonld_listings (worker/src/product_search/adapters/universal_ai.py)
//   - _looks_like_product_url and _looks_like_nav_path (same file)

const FETCH_TIMEOUT_MS = 8000;
const MIN_BODY_LENGTH = 500;

// --- Tier 1.5 detail-extraction mirror (page_type:"detail" only) ----------
// These mirror worker/src/product_search/adapters/universal_ai.py so the
// probe's verdict for a detail URL matches what the runtime adapter will
// actually extract in production.

const DETAIL_MAX_CHARS = 16000;
const DETAIL_MODEL = 'claude-haiku-4-5';

// Block-level tags whose contents are dropped wholesale before flattening to
// text (mirrors _DETAIL_STRIP_TAGS).
const DETAIL_STRIP_TAGS = [
  'script', 'style', 'noscript', 'template', 'svg',
  'nav', 'header', 'footer', 'iframe',
];

// Amazon-style split price spans flatten to "$ 329 99"; rejoin to "$329.99"
// (mirrors _SPLIT_PRICE_RE / _canonicalize_prices). The "." in the gap covers
// Amazon's literal decimal span.
const SPLIT_PRICE_RE = /\$\s+(\d{1,4}(?:,\d{3})*)[\s.]+(\d{2})\b/g;

// Foreign-currency amounts leaked by AlterLab's European exit IPs (mirrors
// _FOREIGN_CURRENCY_RE) — stripped so the model can't read EUR/GBP as USD.
const FOREIGN_CURRENCY_RE =
  /(?:EUR\s*€|\bEUR[\s ]+(?=\d)|€|GBP\s*£|£|CA?\$|A\$|¥|₹|\bCHF\s+)\s*\d{1,6}(?:[.,]\d{2,3})*/gi;

// Verbatim copy of DETAIL_SYSTEM_PROMPT from universal_ai.py — the probe must
// ask the model exactly what the runtime asks so the verdict matches.
const DETAIL_SYSTEM_PROMPT = `You are extracting THE single product from one vendor product-detail page.

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
`;

// Hosts known to AlterLab-render fine in production even when the bare TS
// ``fetch`` from a Vercel datacenter IP gets a 5xx or a bot-block. Production
// uses AlterLab (see worker/.../universal_ai.py + docs/VENDOR_REACH.md), so
// demoting these hosts based on a 503 from the save-time probe would be a
// false negative. The probe still surfaces the failure as a *warning* in
// reports[].reason but does not move the source to ``sources_pending``.
//
// The list is the single-source-of-truth vendor quirks registry
// (worker/src/product_search/vendor_quirks.yaml, `alterlab_known_good: true`), rendered to
// vendor-quirks-data.ts by scripts/sync-prompt.js at build time (ADR-068). Add
// a host by editing the YAML — not here — when both (a) AlterLab can reach it
// and (b) the universal_ai adapter has extracted real candidates in prod.
import { ALTERLAB_KNOWN_GOOD_HOSTS } from '@/lib/onboard/vendor-quirks-data';

function hostOf(url: string): string | null {
  // Normalize to the www-stripped form the registry is keyed on.
  try {
    const h = new URL(url).host.toLowerCase();
    return h.startsWith('www.') ? h.slice(4) : h;
  } catch {
    return null;
  }
}

const NAV_PATHS: ReadonlyArray<string> = [
  '/about', '/contact', '/blog', '/blogs', '/news', '/press',
  '/pages', '/page', '/articles', '/article', '/help', '/support',
  '/policies', '/policy', '/guides', '/guide', '/legal', '/faq',
  '/learn', '/community', '/locations', '/store-locator', '/find-stores',
  '/stores', '/account', '/wishlist', '/cart', '/checkout', '/login',
  '/signin', '/sign-in', '/register', '/track-order', '/orders',
  '/careers', '/jobs', '/feedback', '/reviews',
];

const PRODUCT_URL_SIGNALS: ReadonlyArray<string> = [
  '/product/', '/products/', '/p/', '/dp/', '/item/', '/itm/',
  '/listing/', '/buy/', '/sku/', '/pd/', '/shop/',
  '/w/',    // ThriftBooks work pages: /w/<title>/<id>/
  '/book/', // Biblio, BetterWorldBooks: /book/<slug>/<id>
];

const SEARCH_OR_CATEGORY_SIGNALS: ReadonlyArray<string> = [
  '/search', '?q=', '?query=', '?_nkw=', '/sch/', '/category/',
  '/categories/', '/collections/', '/c/', '/browse/',
];

const CONDITION_MAP: Record<string, string> = {
  newcondition: 'new',
  usedcondition: 'used',
  refurbishedcondition: 'refurbished',
  damagedcondition: 'used',
};

export interface JsonLdListing {
  title: string;
  url: string;
  priceUsd: number;
  condition: string;
}

export interface ProbeResult {
  ok: boolean;
  url: string;
  fetchStatus: number | null;
  bodyLength: number;
  jsonldCount: number;
  anchorCount: number;
  // For page_type:"detail" URLs: true if the Tier 1.5 mirror extracted a
  // verbatim-verified price, false if not, null if not a detail probe (or the
  // probe never reached extraction, e.g. a hard fetch failure). For a detail
  // URL the onboarder must judge extractability by THIS, not anchorCount.
  detailExtractable: boolean | null;
  reason: string | null; // populated when ok=false
}

// --- HTTP fetch with browser-ish headers ----------------------------------

async function fetchHtml(url: string): Promise<{ html: string; status: number }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const resp = await fetch(url, {
      method: 'GET',
      redirect: 'follow',
      signal: controller.signal,
      headers: {
        'User-Agent':
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
          '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        Accept:
          'text/html,application/xhtml+xml,application/xml;q=0.9,' +
          'image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
      },
    });
    const text = await resp.text();
    return { html: text, status: resp.status };
  } finally {
    clearTimeout(timer);
  }
}

async function fetchViaAlterlab(
  url: string,
  apiKey: string,
  options?: { country?: string; min_tier?: number; wait_for?: string }
): Promise<{ html: string; status: number }> {
  const body: Record<string, unknown> = {
    url,
    sync: true,
    formats: ['html'],
    advanced: { render_js: true },
  };
  if (options) {
    if (options.country) body.country = options.country;
    if (options.min_tier) body.min_tier = options.min_tier;
    if (options.wait_for) (body.advanced as Record<string, unknown>).wait_for = options.wait_for;
  }

  const resp = await fetch('https://api.alterlab.io/api/v1/scrape', {
    method: 'POST',
    headers: {
      'X-API-Key': apiKey,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });

  let payload = await resp.json();

  if (resp.status === 202 && payload.job_id) {
    const jobId = payload.job_id;
    for (let i = 0; i < 20; i++) {
      await new Promise(r => setTimeout(r, 3000));
      const jobResp = await fetch(`https://api.alterlab.io/api/v1/jobs/${jobId}`, {
        headers: { 'X-API-Key': apiKey }
      });
      if (jobResp.ok) {
        const jobPayload = await jobResp.json();
        if (jobPayload.status === 'completed' || jobPayload.status === 'failed') {
          payload = jobPayload.result || {};
          break;
        }
      }
    }
  } else if (!resp.ok) {
    throw new Error(`AlterLab API returned HTTP ${resp.status}: ${JSON.stringify(payload)}`);
  }
  const content = payload.content;
  let html = '';
  if (content && typeof content === 'object' && !Array.isArray(content)) {
    html = content.html || '';
  } else if (typeof content === 'string') {
    html = content;
  }

  const originStatus = Number(payload.status_code || 0);
  return { html, status: originStatus };
}

// --- JSON-LD extraction ---------------------------------------------------

function jsonldBlocks(html: string): unknown[] {
  // Match <script type="application/ld+json"> ... </script>. The attribute
  // can have whitespace and quote variations; we permit single, double, or
  // unquoted attribute values. Body stops at the FIRST </script> — JSON-LD
  // bodies must not contain a literal "</script>" (rare and a spec
  // violation when they do).
  const re = /<script\s+[^>]*type\s*=\s*['"]?application\/ld\+json['"]?[^>]*>([\s\S]*?)<\/script>/gi;
  const out: unknown[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    const raw = m[1].trim();
    if (!raw) continue;
    try {
      out.push(JSON.parse(raw));
    } catch {
      // Skip blocks with comments or trailing commas — vendors do this.
    }
  }
  return out;
}

function* walkJsonld(node: unknown): Generator<Record<string, unknown>> {
  if (Array.isArray(node)) {
    for (const item of node) yield* walkJsonld(item);
  } else if (typeof node === 'object' && node !== null) {
    const obj = node as Record<string, unknown>;
    yield obj;
    for (const v of Object.values(obj)) yield* walkJsonld(v);
  }
}

function hasType(obj: Record<string, unknown>, name: string): boolean {
  const t = obj['@type'];
  if (typeof t === 'string') return t === name;
  if (Array.isArray(t)) return t.includes(name);
  return false;
}

function coercePrice(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'number') {
    return value > 0 && Number.isFinite(value) ? value : null;
  }
  if (typeof value === 'string') {
    // Strip everything except digits, comma, dot, minus.
    const m = value.match(/\d+(?:[.,]\d+)?/);
    if (!m) return null;
    let s = m[0];
    // European "12,99" → "12.99" when there's exactly one comma and no dot.
    if (s.includes(',') && !s.includes('.')) {
      s = s.replace(',', '.');
    } else {
      s = s.replace(/,/g, '');
    }
    const f = parseFloat(s);
    return Number.isFinite(f) && f > 0 ? f : null;
  }
  return null;
}

function conditionFrom(offer: Record<string, unknown>): string {
  let raw = offer.itemCondition;
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const r = raw as Record<string, unknown>;
    raw = r['@id'] ?? r.name ?? '';
  }
  if (typeof raw !== 'string') return 'new';
  const key = raw.split('/').pop()?.toLowerCase() ?? '';
  return CONDITION_MAP[key] ?? 'new';
}

function offerPriceAndCondition(
  offers: unknown,
): { price: number | null; condition: string } {
  const candidates: Array<{ price: number; condition: string }> = [];
  const consider = (o: Record<string, unknown>): void => {
    if (hasType(o, 'AggregateOffer')) {
      const p = coercePrice(o.lowPrice) ?? coercePrice(o.price);
      if (p !== null) candidates.push({ price: p, condition: conditionFrom(o) });
      return;
    }
    const p = coercePrice(o.price);
    if (p !== null) candidates.push({ price: p, condition: conditionFrom(o) });
  };
  if (Array.isArray(offers)) {
    for (const o of offers) {
      if (typeof o === 'object' && o !== null) consider(o as Record<string, unknown>);
    }
  } else if (typeof offers === 'object' && offers !== null) {
    consider(offers as Record<string, unknown>);
  }
  if (candidates.length === 0) return { price: null, condition: 'new' };
  candidates.sort((a, b) => a.price - b.price);
  return candidates[0];
}

export function extractJsonldListings(html: string, baseUrl: string): JsonLdListing[] {
  const out: JsonLdListing[] = [];
  const seen = new Set<string>();
  let baseParsed: URL;
  try {
    baseParsed = new URL(baseUrl);
  } catch {
    return [];
  }
  for (const block of jsonldBlocks(html)) {
    for (const obj of walkJsonld(block)) {
      if (!hasType(obj, 'Product')) continue;
      const name = obj.name;
      if (typeof name !== 'string' || !name.trim()) continue;
      const title = name.trim().slice(0, 300);

      const urlRaw = obj.url;
      if (typeof urlRaw !== 'string' || !urlRaw.trim()) continue;
      let abs: URL;
      try {
        abs = new URL(urlRaw.trim(), baseParsed);
      } catch {
        continue;
      }
      if (abs.protocol !== 'http:' && abs.protocol !== 'https:') continue;

      const canonical = `${abs.protocol}//${abs.host.toLowerCase()}${abs.pathname.replace(/\/+$/, '')}`;
      if (seen.has(canonical)) continue;

      const { price, condition } = offerPriceAndCondition(obj.offers);
      if (price === null) continue;

      seen.add(canonical);
      out.push({ title, url: abs.toString(), priceUsd: price, condition });
    }
  }
  return out;
}

// --- Anchor candidate count (no merge / context — just a coarse count) ----

function looksLikeProductUrl(href: string): boolean {
  const lower = href.toLowerCase();
  if (PRODUCT_URL_SIGNALS.some((s) => lower.includes(s))) return true;
  try {
    const u = new URL(href);
    const last = u.pathname.split('/').pop() ?? '';
    return last.includes('-') && last.length >= 6;
  } catch {
    return false;
  }
}

function looksLikeNavPath(href: string): boolean {
  let path: string;
  try {
    path = new URL(href).pathname.toLowerCase().replace(/\/+$/, '');
  } catch {
    return false;
  }
  if (path === '' || path === '/') return true;
  for (const nav of NAV_PATHS) {
    if (path === nav || path.startsWith(nav + '/')) return true;
  }
  return false;
}

function isSearchOrCategoryUrl(href: string): boolean {
  const lower = href.toLowerCase();
  return SEARCH_OR_CATEGORY_SIGNALS.some((s) => lower.includes(s));
}

export function countProductAnchors(html: string, baseUrl: string): number {
  // Count UNIQUE canonical (scheme+host+path) URLs from anchors that look
  // like product detail pages and don't look like nav / search / category.
  // We don't reproduce the full Python anchor extractor — this is just a
  // gating heuristic. If the count is ≥3 we trust that the production
  // anchor+LLM tier will yield real listings.
  const re = /<a\s[^>]*href\s*=\s*['"]([^'"]+)['"][^>]*>/gi;
  const seen = new Set<string>();
  let baseParsed: URL;
  try {
    baseParsed = new URL(baseUrl);
  } catch {
    return 0;
  }
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    const raw = m[1].trim();
    if (!raw || raw.startsWith('#') || raw.startsWith('javascript:') ||
        raw.startsWith('mailto:') || raw.startsWith('tel:') || raw.startsWith('data:')) {
      continue;
    }
    let abs: URL;
    try {
      abs = new URL(raw, baseParsed);
    } catch {
      continue;
    }
    if (abs.protocol !== 'http:' && abs.protocol !== 'https:') continue;
    const absStr = abs.toString();
    if (isSearchOrCategoryUrl(absStr) || looksLikeNavPath(absStr)) continue;
    if (!looksLikeProductUrl(absStr)) continue;
    const canonical = `${abs.protocol}//${abs.host.toLowerCase()}${abs.pathname.replace(/\/+$/, '')}`;
    seen.add(canonical);
  }
  return seen.size;
}

// --- Tier 1.5 detail extraction (mirror of universal_ai._extract_detail_listing)

const HTML_ENTITIES: Record<string, string> = {
  '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>',
  '&quot;': '"', '&apos;': "'", '&#39;': "'", '&#36;': '$',
};

function decodeEntities(s: string): string {
  let out = s.replace(/&nbsp;|&amp;|&lt;|&gt;|&quot;|&apos;|&#39;|&#36;/g, (m) => HTML_ENTITIES[m] ?? m);
  // Numeric entities (decimal + hex).
  out = out.replace(/&#(\d+);/g, (_, d) => {
    const code = Number(d);
    return Number.isFinite(code) ? String.fromCodePoint(code) : _;
  });
  out = out.replace(/&#x([0-9a-fA-F]+);/g, (_, h) => {
    const code = parseInt(h, 16);
    return Number.isFinite(code) ? String.fromCodePoint(code) : _;
  });
  return out;
}

// Mirror of _strip_to_main_text. No DOM parser on the edge runtime, so this is
// a regex flatten rather than selectolax — close enough that the model sees
// the price + title and the verbatim guard checks the same haystack.
export function stripToMainText(html: string): string {
  let s = html;
  for (const tag of DETAIL_STRIP_TAGS) {
    s = s.replace(new RegExp(`<${tag}\\b[^>]*>[\\s\\S]*?<\\/${tag}>`, 'gi'), ' ');
    s = s.replace(new RegExp(`<${tag}\\b[^>]*/?>`, 'gi'), ' ');
  }
  s = s.replace(/<[^>]+>/g, ' ');
  s = decodeEntities(s);
  s = s.replace(/\s+/g, ' ').trim();
  // _canonicalize_prices: rejoin "$ 329 99" → "$329.99".
  s = s.replace(SPLIT_PRICE_RE, '$$$1.$2');
  // _strip_foreign_currencies.
  s = s.replace(FOREIGN_CURRENCY_RE, '');
  if (s.length > DETAIL_MAX_CHARS) s = s.slice(0, DETAIL_MAX_CHARS);
  return s;
}

// Mirror of _price_in_text — the ADR-001 anti-hallucination guard. True iff
// `price` occurs verbatim in `text` under the same normalisation the runtime
// uses (strip $, commas, whitespace; accept common printed forms).
export function priceInText(price: number, text: string): boolean {
  const norm = text.replace(/[\s,$]/g, '');
  const forms = new Set<string>();
  forms.add(price.toFixed(2));
  if (price === Math.trunc(price)) {
    forms.add(String(Math.trunc(price)));
    forms.add(`${Math.trunc(price)}.00`);
  }
  const trimmed = price.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  if (trimmed) forms.add(trimmed);
  for (const f of forms) {
    if (f && norm.includes(f)) return true;
  }
  return false;
}

function parseDetailJson(raw: string): Record<string, unknown> | null {
  // The prompt asks for bare JSON, but tolerate fences / prose preambles.
  let s = raw.trim();
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) s = fence[1].trim();
  const start = s.indexOf('{');
  const end = s.lastIndexOf('}');
  if (start === -1 || end === -1 || end <= start) return null;
  try {
    const parsed = JSON.parse(s.slice(start, end + 1));
    return (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed))
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

// Faithful mirror of _extract_detail_listing: one Haiku call on the stripped
// page text, then re-verify the returned price verbatim. Returns true iff a
// priced product was extracted and verified. Any failure (no API key, LLM
// error, no price, price not in text) → false: the runtime would emit nothing
// either, so the URL is genuinely not extractable as a detail page.
async function extractDetailListing(html: string, url: string): Promise<boolean> {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) return false;

  const text = stripToMainText(html);
  if (text.length < 20) return false;

  let responseText = '';
  try {
    const client = new Anthropic({ apiKey });
    const msg = await client.messages.create({
      model: DETAIL_MODEL,
      max_tokens: 1024,
      system: DETAIL_SYSTEM_PROMPT,
      messages: [{ role: 'user', content: text }],
    });
    for (const block of msg.content) {
      if (block.type === 'text') responseText += block.text;
    }
  } catch {
    return false;
  }

  const parsed = parseDetailJson(responseText);
  if (!parsed || !parsed.found) return false;

  const price = Number(parsed.price_usd);
  if (!Number.isFinite(price) || price <= 0) return false;

  // The architectural guard: the model never produces a price the
  // deterministic layer didn't fetch (ADR-001). Verify against the same text.
  if (!priceInText(price, text)) return false;

  const title = typeof parsed.title === 'string' ? parsed.title.trim() : '';
  if (!title) return false;

  void url; // kept for symmetry with the runtime signature / future logging
  return true;
}

// --- Top-level probe ------------------------------------------------------

export async function probeUrl(
  url: string,
  alterlabOptions?: { country?: string; min_tier?: number; wait_for?: string },
  pageType?: 'search' | 'detail',
): Promise<ProbeResult> {
  let result: ProbeResult = {
    ok: false,
    url,
    fetchStatus: null,
    bodyLength: 0,
    jsonldCount: 0,
    anchorCount: 0,
    detailExtractable: null,
    reason: null,
  };
  const host = hostOf(url);
  const isAlterlabKnownGood = host !== null && ALTERLAB_KNOWN_GOOD_HOSTS.has(host);

  try {
    let html: string;
    let status: number;

    if (alterlabOptions && Object.keys(alterlabOptions).length > 0) {
      const apiKey = process.env.ALTERLAB_API_KEY;
      if (!apiKey) {
        result.reason = 'ALTERLAB_API_KEY not configured on server (cannot run rendered probe)';
        return result;
      }
      const fetched = await fetchViaAlterlab(url, apiKey, alterlabOptions);
      html = fetched.html;
      status = fetched.status;
    } else {
      const fetched = await fetchHtml(url);
      html = fetched.html;
      status = fetched.status;
    }

    result = { ...result, fetchStatus: status, bodyLength: html.length };
    // Hard failures only — see the policy comment at the top of the file.
    // Anything in the 2xx/3xx range with a non-trivial body passes; we
    // record JSON-LD / anchor counts as diagnostics but they don't gate.
    if (status >= 400) {
      // HTTP 403, 429, 502, 503, 504 from a bare datacenter-IP fetch are almost always
      // bot-blocks (Cloudflare, Akamai, Datadome, etc.), not a genuine "not found" or "gone".
      // Production uses AlterLab's rendered fetch which handles these fine.
      if (!alterlabOptions && (status === 403 || status === 429 || status === 502 || status === 503 || status === 504)) {
        result = { ...result, ok: true, reason: `bare-fetch HTTP ${status} (likely bot-block; not demoting)` };
        return result;
      }
      // AlterLab-known-good hosts: 5xx / bot-block from the bare-fetch probe
      // is expected (Amazon serves 503 to datacenter IPs, etc.); production
      // AlterLab handles these fine. Record the status as a diagnostic but
      // pass the probe so the source is not demoted to sources_pending.
      if (!alterlabOptions && isAlterlabKnownGood && status >= 500) {
        result = { ...result, ok: true, reason: `bare-fetch HTTP ${status} (host is AlterLab-known-good; not demoting)` };
        return result;
      }
      result.reason = `fetch returned HTTP ${status}`;
      return result;
    }
    if (html.length < MIN_BODY_LENGTH) {
      // Same exemption: a tiny body from Amazon/Walmart is almost certainly
      // a bot-block stub, not a dead URL.
      if (!alterlabOptions && isAlterlabKnownGood) {
        result = { ...result, ok: true, reason: `bare-fetch body ${html.length} chars (host is AlterLab-known-good; not demoting)` };
        return result;
      }
      // Dynamically detect common anti-bot/WAF footprint signatures in the short body.
      const lowerBody = html.toLowerCase();
      const hasSecuritySignature =
        lowerBody.includes('cloudflare') ||
        lowerBody.includes('datadome') ||
        lowerBody.includes('akamai') ||
        lowerBody.includes('perimeterx') ||
        lowerBody.includes('access denied') ||
        lowerBody.includes('security check') ||
        lowerBody.includes('captcha') ||
        lowerBody.includes('turnstile') ||
        lowerBody.includes('ray id') ||
        lowerBody.includes('sucuri') ||
        lowerBody.includes('shield') ||
        lowerBody.includes('px-captcha') ||
        lowerBody.includes('checking your browser') ||
        lowerBody.includes('attention required');
      if (!alterlabOptions && hasSecuritySignature) {
        result = { ...result, ok: true, reason: `bare-fetch body too short (${html.length} chars), but contains bot-block signatures; not demoting` };
        return result;
      }
      result.reason = `response body too short (${html.length} chars)`;
      return result;
    }
    const listings = extractJsonldListings(html, url);
    const anchors = countProductAnchors(html, url);
    let detailExtractable: boolean | null = null;
    if (pageType === 'detail') {
      // For a detail URL, anchorCount is meaningless (~0 is expected). Judge by
      // whether the Tier 1.5 path can pull a verbatim-verified price. JSON-LD
      // with a price is already proof (the runtime Tier 1 would catch it), so
      // skip the LLM call in that case to save a Haiku call.
      detailExtractable = listings.length > 0 ? true : await extractDetailListing(html, url);
    }
    result = {
      ...result,
      jsonldCount: listings.length,
      anchorCount: anchors,
      detailExtractable,
      ok: true,
    };
    return result;
  } catch (err) {
    if (!alterlabOptions && isAlterlabKnownGood) {
      result.ok = true;
      result.reason = `bare-fetch failed (${err instanceof Error ? err.message : String(err)}); host is AlterLab-known-good — not demoting`;
      return result;
    }
    result.reason = `fetch failed: ${err instanceof Error ? err.message : String(err)}`;
    return result;
  }
}
