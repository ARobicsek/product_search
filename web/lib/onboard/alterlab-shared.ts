// Pure, dependency-free helpers shared between the onboarder probe
// (probe-url.ts) and the AlterLab-parity CI guard (scripts/check-alterlab-parity.mjs).
//
// CRITICAL: this module MUST stay free of `server-only`, the Anthropic SDK, and
// any Node/Next-runtime import, so it can be loaded by a plain `node --test`
// process (the T5 parity guard, ADR-071). It is the TS mirror of the Python
// runtime adapter's wire-body builder + weak-render predicate + escalation
// ladder + detail strip/price-verify. The parity test asserts these produce the
// same AlterLab request body and the same strip→price-verify verdict as
// worker/src/product_search/adapters/universal_ai.py for the same inputs —
// which would have caught the missing `asp` (ADR-070) instantly.

// AlterLab render options the onboarder / registry may attach to a source.
// `wait_for` is accepted only for back-compat with serialized profiles and is
// migrated to `wait_condition` (ADR-071) — never sent to the wire.
export interface AlterlabOptions {
  country?: string;
  min_tier?: number;
  wait_condition?: string;
  /** @deprecated legacy; migrated to wait_condition (ADR-071) */
  wait_for?: string | number;
}

// Valid AlterLab `advanced.wait_condition` values (docs/ALTERLAB_OPTIONS.md).
export const VALID_WAIT_CONDITIONS = new Set(['domcontentloaded', 'networkidle', 'load']);

// Faithful TS mirror of universal_ai._fetch_via_alterlab's body construction
// (ADR-070/071). The T5 parity test asserts this equals the Python body.
export function buildAlterlabBody(
  url: string,
  options?: AlterlabOptions,
): Record<string, unknown> {
  const advanced: Record<string, unknown> = { render_js: true };
  const body: Record<string, unknown> = {
    url,
    sync: true,
    formats: ['html'],
    // asp = AlterLab anti-scraping/anti-bot bypass. The runtime adapter always
    // sends this; without it AlterLab returns partial / Cloudflare-challenge
    // renders and the probe falsely reports detailExtractable:false (ADR-070).
    asp: true,
    advanced,
  };
  if (options) {
    // ADR-071: the DOCUMENTED nested wire shape. The flat internal keys
    // (country / min_tier) map to location.country / cost_controls.max_tier —
    // the only shape that reliably renders hard sites (Target detail 3/3 vs
    // legacy flat 0/3). cost_controls.max_tier (string) escalates UP TO that
    // tier while returning a fast sync 200, unlike legacy top-level min_tier:4.
    if (options.country) body.location = { country: options.country };
    if (options.min_tier != null) {
      const tier = Math.max(1, Math.min(4, Math.trunc(options.min_tier)));
      body.cost_controls = { max_tier: String(tier) };
    }
    // `wait_for` is a non-existent AlterLab param (forces a never-completing
    // 202 job -> body 0). Migrate legacy values to the real
    // `advanced.wait_condition`, validate, and never send `wait_for`.
    let wc = options.wait_condition;
    if (wc == null && options.wait_for != null) wc = 'networkidle';
    if (wc != null && VALID_WAIT_CONDITIONS.has(wc)) advanced.wait_condition = wc;
  }
  return body;
}

// Mirror of universal_ai._WEAK_RENDER_SIGNATURES (ADR-071): distinctive
// anti-bot / error-stub phrases that mean a 200 body is unusable.
export const WEAK_RENDER_SIGNATURES =
  /just a moment|checking your browser|attention required|please enable (?:js|javascript) and cookies|enable javascript to continue|there was a temporary issue|\/cdn-cgi\/challenge|captcha-delivery|px-captcha|verify you are (?:a )?human|access to this page has been denied/i;
export const WEAK_BODY_FLOOR = 2000;

// Mirror of universal_ai._weak_render_reason.
export function weakRenderReason(html: string, status: number): string | null {
  if (!html) return `empty body (origin status=${status})`;
  if (status && status >= 400) return `origin HTTP ${status}`;
  if (html.length < WEAK_BODY_FLOOR) return `body too short (${html.length} chars)`;
  if (WEAK_RENDER_SIGNATURES.test(html)) return 'anti-bot challenge / error-stub signature in body';
  return null;
}

// Mirror of universal_ai._escalation_ladder. Tier-4 escalation goes through the
// documented cost_controls.max_tier body shape (buildAlterlabBody maps min_tier
// -> cost_controls.max_tier), which returns a fast sync 200 — NOT the legacy
// top-level min_tier:4 that the R2 matrix proved 202-hangs. See ADR-071.
export function alterlabEscalationLadder(base?: AlterlabOptions): AlterlabOptions[] {
  const rungs: AlterlabOptions[] = [{ ...(base ?? {}) }];
  const last = () => rungs[rungs.length - 1];
  if (last().wait_condition !== 'networkidle') {
    rungs.push({ ...last(), wait_condition: 'networkidle' });
  }
  if (last().min_tier !== 4) {
    rungs.push({ ...last(), min_tier: 4 });
  }
  return rungs;
}

// --- Detail strip + price-verify (mirror of universal_ai) ------------------

export const DETAIL_MAX_CHARS = 16000;

// Block-level tags whose contents are dropped wholesale (mirrors _DETAIL_STRIP_TAGS).
export const DETAIL_STRIP_TAGS = [
  'script', 'style', 'noscript', 'template', 'svg',
  'nav', 'header', 'footer', 'iframe',
];

// Amazon-style split price spans flatten to "$ 329 99"; rejoin to "$329.99".
export const SPLIT_PRICE_RE = /\$\s+(\d{1,4}(?:,\d{3})*)[\s.]+(\d{2})\b/g;

// Foreign-currency amounts leaked by AlterLab's European exit IPs.
export const FOREIGN_CURRENCY_RE =
  /(?:EUR\s*€|\bEUR[\s ]+(?=\d)|€|GBP\s*£|£|CA?\$|A\$|¥|₹|\bCHF\s+)\s*\d{1,6}(?:[.,]\d{2,3})*/gi;

const HTML_ENTITIES: Record<string, string> = {
  '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>',
  '&quot;': '"', '&apos;': "'", '&#39;': "'", '&#36;': '$',
};

export function decodeEntities(s: string): string {
  let out = s.replace(/&nbsp;|&amp;|&lt;|&gt;|&quot;|&apos;|&#39;|&#36;/g, (m) => HTML_ENTITIES[m] ?? m);
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
  s = s.replace(SPLIT_PRICE_RE, '$$$1.$2');
  s = s.replace(FOREIGN_CURRENCY_RE, '');
  if (s.length > DETAIL_MAX_CHARS) s = s.slice(0, DETAIL_MAX_CHARS);
  return s;
}

// Mirror of _price_in_text — the ADR-001 anti-hallucination guard.
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
