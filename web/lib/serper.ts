/**
 * Serper.dev shopping-search client (Phase 34, ADR-137).
 *
 * TS mirror of `worker/src/product_search/adapters/serper.py`. Used by the v2
 * onboarder's one-shot live preview tool (REBUILD_PLAN §6): the onboarder fires
 * ONE real Serper query so the model AND the user can confirm the query + match
 * spec actually surface the item before saving — this single cheap honest
 * preview replaces the entire v1 probe/backfill/save-modal apparatus.
 *
 * Honesty (ADR-001): every field is copied verbatim from Serper's structured
 * response. `link` is the Google Shopping cluster redirect Serper returns — we
 * never fabricate a merchant URL or a price.
 *
 * Edge-runtime safe: uses the global `fetch` only (the chat route runs on the
 * edge runtime), no node-only APIs.
 */

const SERPER_SHOPPING_URL = 'https://google.serper.dev/shopping';
const SERPER_TIMEOUT_MS = 30_000;

export interface SerperItem {
  title: string;
  merchant: string | null; // Serper "source" — the store/merchant name
  link: string; // Google Shopping cluster redirect
  price: number | null; // parsed USD
  priceText: string | null; // verbatim Serper price string, e.g. "$599.00"
  rating: number | null;
  ratingCount: number | null;
  productId: string | null;
  imageUrl: string | null;
}

export interface SerperPreview {
  query: string;
  ok: boolean;
  count: number;
  items: SerperItem[];
  error?: string;
}

/** Parse Serper's `price` (number or "$1,299.00" string) into a float + the raw text. */
export function parseSerperPrice(price: unknown): { value: number | null; text: string | null } {
  if (price === null || price === undefined) return { value: null, text: null };
  if (typeof price === 'number') {
    return { value: Number.isFinite(price) ? price : null, text: String(price) };
  }
  const text = String(price).trim();
  if (!text) return { value: null, text: null };
  const cleaned = text.replace(/[^0-9.]/g, '');
  const v = Number.parseFloat(cleaned);
  return { value: Number.isFinite(v) ? v : null, text };
}

/** Map a single Serper shopping item to a compact `SerperItem` (null on no title). */
export function mapSerperItem(item: Record<string, unknown>): SerperItem | null {
  const title = typeof item.title === 'string' ? item.title.trim() : '';
  if (!title) return null;
  const { value, text } = parseSerperPrice(item.price);
  const link = typeof item.link === 'string' ? item.link.trim() : '';
  const merchant = typeof item.source === 'string' ? item.source.trim() : '';
  const imageUrl = typeof item.imageUrl === 'string' ? item.imageUrl.trim() : '';
  let productId: string | null = null;
  if (typeof item.productId === 'string') productId = item.productId.trim() || null;
  else if (item.productId != null) productId = String(item.productId);
  return {
    title,
    merchant: merchant || null,
    link,
    price: value,
    priceText: text,
    rating: typeof item.rating === 'number' ? item.rating : null,
    ratingCount: typeof item.ratingCount === 'number' ? item.ratingCount : null,
    productId,
    imageUrl: imageUrl || null,
  };
}

/**
 * Map a Serper shopping response (`{ shopping: [...] }`) to `SerperItem[]`,
 * de-duped by productId → link → title (mirrors the Python adapter's dedup).
 */
export function mapSerperResponse(json: unknown): SerperItem[] {
  const shopping =
    json && typeof json === 'object' && Array.isArray((json as { shopping?: unknown }).shopping)
      ? ((json as { shopping: unknown[] }).shopping)
      : [];
  const out: SerperItem[] = [];
  const seen = new Set<string>();
  for (const raw of shopping) {
    if (!raw || typeof raw !== 'object') continue;
    const mapped = mapSerperItem(raw as Record<string, unknown>);
    if (!mapped) continue;
    const key = mapped.productId || mapped.link || mapped.title;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(mapped);
  }
  return out;
}

/**
 * Fire one Serper shopping query and return the mapped, de-duped results.
 * Never throws — network/auth failures come back as `{ ok: false, error }` so
 * the onboarder can surface an honest message instead of crashing the turn.
 */
export async function serperShoppingPreview(
  query: string,
  opts?: { gl?: string; num?: number; apiKey?: string },
): Promise<SerperPreview> {
  const q = (query ?? '').trim();
  if (!q) {
    return { query, ok: false, count: 0, items: [], error: 'empty query' };
  }
  const apiKey = opts?.apiKey ?? process.env.SERPER_API_KEY;
  if (!apiKey) {
    return { query: q, ok: false, count: 0, items: [], error: 'SERPER_API_KEY not configured on server' };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SERPER_TIMEOUT_MS);
  try {
    const resp = await fetch(SERPER_SHOPPING_URL, {
      method: 'POST',
      headers: { 'X-API-KEY': apiKey, 'Content-Type': 'application/json' },
      body: JSON.stringify({ q, gl: opts?.gl ?? 'us', num: opts?.num ?? 20 }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      return { query: q, ok: false, count: 0, items: [], error: `Serper HTTP ${resp.status}` };
    }
    const json: unknown = await resp.json();
    const items = mapSerperResponse(json);
    return { query: q, ok: true, count: items.length, items };
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'serper request failed';
    return { query: q, ok: false, count: 0, items: [], error: msg };
  } finally {
    clearTimeout(timer);
  }
}
