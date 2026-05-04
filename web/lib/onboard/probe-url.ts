import 'server-only';

// TypeScript port of the worker's universal_ai probe (Phase 15 task 5).
//
// Why a TS port and not a subprocess to the Python CLI: the onboarder runs
// in the Vercel Node runtime, where forking subprocesses is unavailable on
// the edge tier and slow on the Node tier. Re-implementing the probe in TS
// keeps the save-time gate fast (parallel fetches, no Python boot) and
// avoids adding a worker-deployment dependency to the web app.
//
// What we DON'T port: the anchor + LLM tier from
// worker/src/product_search/adapters/universal_ai.py. The TS probe is
// deliberately a subset — we only detect URLs that yield JSON-LD Product
// blocks OR contain enough product-URL-shaped anchors to be plausibly
// extractable in production. Anything else gets routed to
// ``sources_pending`` with the "probe returned 0 candidates" note so the
// user can review before the worker tries to run them.
//
// Mirrors:
//   - _extract_jsonld_listings (worker/src/product_search/adapters/universal_ai.py)
//   - _looks_like_product_url and _looks_like_nav_path (same file)

const FETCH_TIMEOUT_MS = 8000;
const MIN_BODY_LENGTH = 1000;
const MIN_ANCHOR_CANDIDATES = 3;

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

// --- Top-level probe ------------------------------------------------------

export async function probeUrl(url: string): Promise<ProbeResult> {
  let result: ProbeResult = {
    ok: false,
    url,
    fetchStatus: null,
    bodyLength: 0,
    jsonldCount: 0,
    anchorCount: 0,
    reason: null,
  };
  try {
    const { html, status } = await fetchHtml(url);
    result = { ...result, fetchStatus: status, bodyLength: html.length };
    if (status < 200 || status >= 400) {
      result.reason = `fetch returned HTTP ${status}`;
      return result;
    }
    if (html.length < MIN_BODY_LENGTH) {
      result.reason = `response body too short (${html.length} chars; likely a challenge page)`;
      return result;
    }
    const listings = extractJsonldListings(html, url);
    const anchors = countProductAnchors(html, url);
    result = { ...result, jsonldCount: listings.length, anchorCount: anchors };
    if (listings.length >= 1 || anchors >= MIN_ANCHOR_CANDIDATES) {
      result.ok = true;
      return result;
    }
    result.reason = 'probe returned 0 candidates';
    return result;
  } catch (err) {
    result.reason = `fetch failed: ${err instanceof Error ? err.message : String(err)}`;
    return result;
  }
}
