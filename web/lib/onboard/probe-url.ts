import 'server-only';

import Anthropic from '@anthropic-ai/sdk';

// Pure, dependency-free AlterLab helpers live in alterlab-shared.ts so the T5
// parity guard can import them from a plain node process (ADR-071).
import {
  type AlterlabOptions,
  buildAlterlabBody,
  weakRenderReason,
  alterlabEscalationLadder,
  stripToMainText,
  priceInText,
} from '@/lib/onboard/alterlab-shared';

export type { AlterlabOptions } from '@/lib/onboard/alterlab-shared';
export { stripToMainText, priceInText, buildAlterlabBody } from '@/lib/onboard/alterlab-shared';

// ADR-116: deterministic detail-page relevance gate. titleMatchesTarget lives
// in a pure module so the offline guard suite can test it without the Anthropic
// SDK / server-only dependencies this file carries.
import { titleMatchesTarget } from '@/lib/onboard/detail-title-match';

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
// The strip + price-verify helpers live in alterlab-shared.ts (imported above);
// only the LLM model + prompt stay here (they pull in the Anthropic SDK).

const DETAIL_MODEL = 'claude-haiku-4-5';


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
  // ADR-098 fix #1: how many product-anchor texts contain a distinctive token
  // from the target product (model number, brand).  Advisory — used by the
  // onboarder to detect mis-scoped search URLs (many anchors, 0 relevance).
  relevanceHits: number;
  // For page_type:"detail" URLs: true if the Tier 1.5 mirror extracted a
  // verbatim-verified price, false if not, null if not a detail probe (or the
  // probe never reached extraction, e.g. a hard fetch failure). For a detail
  // URL the onboarder must judge extractability by THIS, not anchorCount.
  detailExtractable: boolean | null;
  // ADR-116: for page_type:"detail" URLs, whether the page's (verbatim)
  // product title carries the target product's family-root tokens. true =
  // right product, false = a DIFFERENT product (e.g. the DJI Transceiver page
  // baked into the Neo 2 profile), null = not a detail probe / no target name /
  // not extractable (no title to judge). When false, the probe fails (ok=false).
  detailTitleMatch: boolean | null;
  reason: string | null; // populated when ok=false
  jsonldListings?: JsonLdListing[];
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
  options?: AlterlabOptions,
): Promise<{ html: string; status: number }> {
  const body = buildAlterlabBody(url, options);

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

// --- ADR-098 fix #1: relevance-hit counting for search URLs ---------------

/**
 * Tokenize a product name into "distinctive" tokens used to score whether a
 * search page's anchors actually reference the target product.
 *
 * ADR-099 fix #7: PREFER model-number-shaped tokens — a word containing BOTH a
 * letter and a digit (e.g. ``H14SSL-N``), normalized to lowercase-alphanumeric
 * (``h14ssln``). The previous version returned every ≥3-char word including the
 * bare brand (``supermicro``) and category (``motherboard``) words, so a
 * vendor's catalog of "Supermicro <accessory>" rows scored a relevance hit on
 * the brand alone — the false "11 relevance hits" that greenlit a dead GoToDirect
 * source. Only when the name has NO model token do we fall back to the old
 * generic-word behavior (so products without a model number still get a signal).
 */
function distinctiveTokens(name: string): string[] {
  const STOP_WORDS = new Set([
    'the', 'and', 'for', 'with', 'new', 'set', 'kit', 'pack', 'pair',
    'lot', 'box', 'qty', 'pcs', 'item', 'items', 'product', 'products',
  ]);
  const words = name.toLowerCase().split(/[\s,.()+]+/).filter(Boolean);
  const modelTokens = words
    .filter(w => /[a-z]/.test(w) && /\d/.test(w))
    .map(w => w.replace(/[^a-z0-9]+/g, ''))
    .filter(w => w.length >= 4);
  if (modelTokens.length > 0) return modelTokens;
  return words
    .flatMap(w => w.split(/[-/]/))
    .filter(t => t.length >= 3 && !STOP_WORDS.has(t));
}

/**
 * Count how many product-anchor texts contain at least one distinctive token
 * from `targetName`. Returns 0 when `targetName` is empty/undefined.
 */
export function countRelevanceHits(
  html: string,
  baseUrl: string,
  targetName: string | undefined,
): number {
  if (!targetName?.trim()) return 0;
  const tokens = distinctiveTokens(targetName);
  if (tokens.length === 0) return 0;

  // Regex that captures both href and the visible anchor text.
  const re = /<a\s[^>]*href\s*=\s*['"]([^'"]+)['"][^>]*>([\s\S]*?)<\/a>/gi;
  let baseParsed: URL;
  try {
    baseParsed = new URL(baseUrl);
  } catch {
    return 0;
  }

  const seen = new Set<string>();
  let hits = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    const rawHref = m[1].trim();
    const anchorText = m[2].replace(/<[^>]*>/g, '').trim().toLowerCase();
    if (!rawHref || !anchorText) continue;

    // Dedupe by canonical URL (same as countProductAnchors)
    let abs: URL;
    try {
      abs = new URL(rawHref, baseParsed);
    } catch {
      continue;
    }
    if (abs.protocol !== 'http:' && abs.protocol !== 'https:') continue;
    const canonical = `${abs.protocol}//${abs.host.toLowerCase()}${abs.pathname.replace(/\/+$/, '')}`;
    if (seen.has(canonical)) continue;
    seen.add(canonical);

    // Skip nav / search / category links — same filter as countProductAnchors
    const absStr = abs.toString();
    if (isSearchOrCategoryUrl(absStr) || looksLikeNavPath(absStr)) continue;
    if (!looksLikeProductUrl(absStr)) continue;

    // Match the distinctive tokens against the anchor text with separators
    // stripped (ADR-099), so a normalized model token like "h14ssln" matches
    // an anchor that renders the product as "H14SSL-N".
    const anchorNorm = anchorText.replace(/[^a-z0-9]+/g, '');
    if (tokens.some(t => anchorNorm.includes(t))) {
      hits++;
    }
  }
  return hits;
}

// --- Tier 1.5 detail extraction (mirror of universal_ai._extract_detail_listing)
// stripToMainText / priceInText now live in alterlab-shared.ts (imported above).

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
// page text, then re-verify the returned price verbatim. Returns the extracted
// product's VERBATIM title alongside `extractable` so the caller can run the
// ADR-116 relevance gate. Any failure (no API key, LLM error, no price, price
// not in text) → { extractable: false }: the runtime would emit nothing either,
// so the URL is genuinely not extractable as a detail page.
//
// The title is pulled verbatim from the page text by the extractor — never
// fabricated (ADR-001). The relevance JUDGMENT is made deterministically by the
// caller (titleMatchesTarget), not by the model.
async function extractDetailListing(
  html: string,
  url: string,
): Promise<{ extractable: boolean; title: string | null }> {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) return { extractable: false, title: null };

  const text = stripToMainText(html);
  if (text.length < 20) return { extractable: false, title: null };

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
    return { extractable: false, title: null };
  }

  const parsed = parseDetailJson(responseText);
  if (!parsed || !parsed.found) return { extractable: false, title: null };

  const price = Number(parsed.price_usd);
  if (!Number.isFinite(price) || price <= 0) return { extractable: false, title: null };

  // The architectural guard: the model never produces a price the
  // deterministic layer didn't fetch (ADR-001). Verify against the same text.
  if (!priceInText(price, text)) return { extractable: false, title: null };

  const title = typeof parsed.title === 'string' ? parsed.title.trim() : '';
  if (!title) return { extractable: false, title: null };

  void url; // kept for symmetry with the runtime signature / future logging
  return { extractable: true, title };
}

// --- Top-level probe ------------------------------------------------------

export async function probeUrl(
  url: string,
  alterlabOptions?: AlterlabOptions,
  pageType?: 'search' | 'detail',
  targetName?: string,
): Promise<ProbeResult> {
  let result: ProbeResult = {
    ok: false,
    url,
    fetchStatus: null,
    bodyLength: 0,
    jsonldCount: 0,
    anchorCount: 0,
    relevanceHits: 0,
    detailExtractable: null,
    detailTitleMatch: null,
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
      // ADR-071: retry-on-weak-render with bounded escalation, mirroring the
      // runtime adapter (_fetch_with_escalation). A single unlucky fetch (a
      // Cloudflare "Just a moment…" challenge, Target's "temporary issue" stub)
      // used to make the probe report detailExtractable:false and silently drop
      // a perfectly-good detail backup. Re-fetch with progressively stronger
      // options; stop at the first non-weak body, else keep the largest.
      const ladder = alterlabEscalationLadder(alterlabOptions);
      let best: { html: string; status: number } | null = null;
      for (const opts of ladder) {
        const fetched = await fetchViaAlterlab(url, apiKey, opts);
        if (!weakRenderReason(fetched.html, fetched.status)) {
          best = fetched;
          break;
        }
        if (!best || fetched.html.length > best.html.length) best = fetched;
      }
      html = best!.html;
      status = best!.status;
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
    let detailTitleMatch: boolean | null = null;
    if (pageType === 'detail') {
      // For a detail URL, anchorCount is meaningless (~0 is expected). Judge by
      // whether the Tier 1.5 path can pull a verbatim-verified price. JSON-LD
      // with a price is already proof (the runtime Tier 1 would catch it), so
      // skip the LLM call in that case to save a Haiku call.
      let detailTitle: string | null = null;
      if (listings.length > 0) {
        detailExtractable = true;
        detailTitle = listings[0].title;
      } else {
        const extracted = await extractDetailListing(html, url);
        detailExtractable = extracted.extractable;
        detailTitle = extracted.title;
      }
      // ADR-116: relevance gate. Judge the verbatim title against the target
      // product name. A different product (the DJI Transceiver page baked into
      // the Neo 2 profile) shares only the brand and fails here.
      if (detailExtractable && detailTitle) {
        detailTitleMatch = titleMatchesTarget(detailTitle, targetName);
      }
      if (detailTitleMatch === false) {
        result = {
          ...result,
          jsonldCount: listings.length,
          anchorCount: anchors,
          detailExtractable,
          detailTitleMatch,
          jsonldListings: listings,
          ok: false,
          reason:
            `detail page is for a different product (extracted title: ` +
            `${JSON.stringify(detailTitle)}; does not match "${targetName}")`,
        };
        return result;
      }
    }
    result = {
      ...result,
      jsonldCount: listings.length,
      anchorCount: anchors,
      relevanceHits: pageType !== 'detail' ? countRelevanceHits(html, url, targetName) : 0,
      detailExtractable,
      detailTitleMatch,
      ok: true,
      jsonldListings: listings,
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
