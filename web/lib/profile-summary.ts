// Pure, read-only summarizer for a v2 profile.yaml (no LLM, no network).
//
// The product page already has the committed `profile.yaml` text in hand
// (fetched server-side for the Schedule & Alerts editor). This turns that
// string into friendly, display-ready pieces so a user can *view* what a slug
// tracks without opening the onboarder (which fires a paid LLM turn on mount).
//
// Schedule and alerts are intentionally NOT summarized here — the component
// composes those from the existing `humanizeSchedule` / `describeRule` helpers
// (schedule needs the browser time zone, which would make this impure). This
// module stays deterministic and unit-testable. Mirrors the v2 schema field
// names in `worker/src/product_search/profile_v2.py`.

import yaml from 'js-yaml';

export interface SourceSummary {
  label: string;
  enabled: boolean;
  detail: string | null;
}

export interface ProfileSummary {
  /** True when the YAML parsed into an object. When false, only `raw` and
   *  `parseError` are meaningful — the caller should show the raw view. */
  ok: boolean;
  parseError: string | null;
  raw: string;

  displayName: string | null;
  slug: string | null;
  productType: string | null;
  description: string | null;
  schemaVersion: number | null;

  target: string | null;
  queries: string[];

  aliases: string[];
  titleExcludes: string[];
  variantStrict: boolean | null;

  filters: string[];
  sources: SourceSummary[];
  flags: string[];

  maxListings: number | null;
  perVendorCap: number | null;
  displayAttrs: string[];

  vendorAllowlist: string[];
  vendorBlocklist: string[];
}

function isObj(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function asStringList(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x): x is string => typeof x === 'string' && x.trim().length > 0).map((x) => x.trim());
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** "low_seller_feedback" -> "Low seller feedback". */
export function prettifyKey(key: string): string {
  const s = key.replace(/[_-]+/g, ' ').trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : key;
}

/** Human list with a trailing conjunction: ["a","b","c"] -> "a, b or c". */
function joinWith(items: string[], conj: 'or' | 'and'): string {
  if (items.length === 0) return '';
  if (items.length === 1) return items[0];
  return `${items.slice(0, -1).join(', ')} ${conj} ${items[items.length - 1]}`;
}

/** Friendly one-liner for one `filters:` block. */
function summarizeFilters(filters: Record<string, unknown>): string[] {
  const out: string[] = [];

  const conditions = asStringList(filters.condition_in);
  if (conditions.length === 1) {
    out.push(`${prettifyKey(conditions[0])} only`);
  } else if (conditions.length > 1) {
    // Sentence-case prose reads better than per-word caps for a list.
    const phrase = joinWith(conditions.map((c) => c.toLowerCase()), 'or');
    out.push(phrase.charAt(0).toUpperCase() + phrase.slice(1));
  }

  if (filters.in_stock === true) out.push('In stock only');

  const minQty = num(filters.min_quantity);
  if (minQty !== null) out.push(`At least ${minQty} available`);

  return out;
}

/** Friendly one-liner for one `flags:` entry, e.g. {low_seller_feedback:{rating_pct_below:98}}. */
function summarizeFlag(entry: unknown): string | null {
  if (typeof entry === 'string') return prettifyKey(entry);
  if (!isObj(entry)) return null;
  const keys = Object.keys(entry);
  if (keys.length === 0) return null;
  const name = keys[0];
  const params = entry[name];
  const label = prettifyKey(name);
  if (isObj(params)) {
    const bits = Object.entries(params).map(([k, v]) => `${k.replace(/_/g, ' ')} ${String(v)}`);
    if (bits.length > 0) return `${label} (${bits.join(', ')})`;
  }
  return label;
}

function summarizeTarget(target: unknown): string | null {
  if (!isObj(target)) return null;
  const amount = num(target.amount);
  const unit = typeof target.unit === 'string' ? target.unit.trim() : '';
  if (amount === null && !unit) return null;
  if (amount === null) return unit || null;
  if (!unit) return String(amount);
  const plural = amount === 1 || /s$/i.test(unit) ? unit : `${unit}s`;
  return `${amount} ${plural}`;
}

function summarizeSources(sources: unknown): SourceSummary[] {
  const s = isObj(sources) ? sources : {};
  const serper = isObj(s.serper) ? s.serper : {};
  const ebay = isObj(s.ebay) ? s.ebay : {};
  const amazon = isObj(s.amazon) ? s.amazon : {};

  // Defaults mirror profile_v2.py: serper on, ebay/amazon off.
  const serperOn = serper.enabled !== false;
  const ebayOn = ebay.enabled === true;
  const amazonOn = amazon.enabled === true;

  const serperNum = num(serper.num);
  const gl = typeof serper.gl === 'string' ? serper.gl : null;
  const amazonPriority = typeof amazon.priority === 'string' ? amazon.priority : null;

  return [
    {
      label: 'Google Shopping (Serper)',
      enabled: serperOn,
      detail: serperOn
        ? [gl ? gl.toUpperCase() : null, serperNum !== null ? `${serperNum} results` : null]
            .filter(Boolean)
            .join(' · ') || null
        : null,
    },
    { label: 'eBay', enabled: ebayOn, detail: null },
    {
      label: 'Amazon',
      enabled: amazonOn,
      detail: amazonOn && amazonPriority ? `${amazonPriority} queue` : null,
    },
  ];
}

export function summarizeProfile(yamlText: string | null): ProfileSummary {
  const raw = yamlText ?? '';
  const empty: ProfileSummary = {
    ok: false,
    parseError: null,
    raw,
    displayName: null,
    slug: null,
    productType: null,
    description: null,
    schemaVersion: null,
    target: null,
    queries: [],
    aliases: [],
    titleExcludes: [],
    variantStrict: null,
    filters: [],
    sources: [],
    flags: [],
    maxListings: null,
    perVendorCap: null,
    displayAttrs: [],
    vendorAllowlist: [],
    vendorBlocklist: [],
  };

  if (!yamlText || !yamlText.trim()) {
    return { ...empty, parseError: 'No profile found.' };
  }

  let parsed: unknown;
  try {
    parsed = yaml.load(yamlText);
  } catch (err) {
    return { ...empty, parseError: err instanceof Error ? err.message : 'Could not parse the profile YAML.' };
  }
  if (!isObj(parsed)) {
    return { ...empty, parseError: 'The profile is not a YAML mapping.' };
  }

  const match = isObj(parsed.match) ? parsed.match : {};
  const filters = isObj(parsed.filters) ? parsed.filters : {};
  const display = isObj(parsed.display) ? parsed.display : {};

  const flags = Array.isArray(parsed.flags)
    ? parsed.flags.map(summarizeFlag).filter((x): x is string => x !== null)
    : [];

  return {
    ok: true,
    parseError: null,
    raw,
    displayName: typeof parsed.display_name === 'string' ? parsed.display_name.trim() : null,
    slug: typeof parsed.slug === 'string' ? parsed.slug.trim() : null,
    productType: typeof parsed.product_type === 'string' ? parsed.product_type.trim() : null,
    description: typeof parsed.description === 'string' ? parsed.description.trim() : null,
    schemaVersion: num(parsed.schema_version),
    target: summarizeTarget(parsed.target),
    queries: asStringList(parsed.queries),
    aliases: asStringList(match.aliases),
    titleExcludes: asStringList(match.title_excludes),
    variantStrict: typeof match.variant_strict === 'boolean' ? match.variant_strict : null,
    filters: summarizeFilters(filters),
    sources: summarizeSources(parsed.sources),
    flags,
    maxListings: num(display.max_listings),
    perVendorCap: num(display.per_vendor_cap),
    displayAttrs: asStringList(display.attrs),
    vendorAllowlist: asStringList(parsed.vendor_allowlist),
    vendorBlocklist: asStringList(parsed.vendor_blocklist),
  };
}
