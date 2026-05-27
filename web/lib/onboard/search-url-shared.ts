// ADR-105: deterministic vendor search-URL rendering, shared with the worker's
// `render_search_url` (worker/.../vendor_quirks.py) and parity-checked by
// scripts/check-search-url-parity.test.mjs. The template + its `{q}` keyword
// param name come from the registry (vendor_quirks.yaml -> SEARCH_URL_TEMPLATES),
// never from an LLM guess — that's the whole point of ADR-105.
//
// This module is intentionally self-contained (no `@/` alias / sibling imports)
// so the node --test parity runner can load it directly, matching the
// alterlab-shared.ts convention. Callers pass the SEARCH_URL_TEMPLATES map from
// vendor-quirks-data.ts.

export function normalizeHost(host: string): string {
  let h = (host || '').toLowerCase().trim();
  if (h.startsWith('www.')) h = h.slice(4);
  return h;
}

// Mirror Python's urllib.parse.quote_plus: spaces -> '+', and the unreserved
// set kept literal is [A-Za-z0-9_.-~]. encodeURIComponent leaves
// `-_.!~*'()` unescaped, so quote_plus and encodeURIComponent differ only on
// `! * ' ( )` (quote_plus escapes them; both keep `~`). We escape those five
// and convert %20 -> '+'.
function quotePlus(value: string): string {
  return encodeURIComponent(value)
    .replace(/[!'()*]/g, (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase())
    .replace(/%20/g, '+');
}

/**
 * Render a vendor's registered search-results URL for `query`, or `null` when
 * the host has no template (caller then constructs a URL itself). Pass the
 * SEARCH_URL_TEMPLATES map from `vendor-quirks-data.ts`.
 */
export function renderSearchUrl(
  host: string,
  query: string,
  templates: Readonly<Record<string, string>>,
): string | null {
  const template = templates[normalizeHost(host)];
  if (!template || !template.includes('{q}')) return null;
  return template.replace('{q}', quotePlus(query.trim()));
}
