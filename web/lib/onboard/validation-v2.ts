import yaml from 'js-yaml';

/**
 * v2 profile-draft validation + YAML render (Phase 34, ADR-137).
 *
 * The v2 onboarder produces a "query + spec" draft (REBUILD_PLAN §4) that loads
 * into the worker's `ProfileV2` model. This module is the web-side gate: it
 * mirrors the parts of `profile_v2.py` that the onboarder can get wrong (queries
 * required, distinctive carry-gate aliases, a real slug, at least one enabled
 * source) and renders the canonical `schema_version: 2` YAML.
 *
 * It deliberately drops the v1 probe-era guards (force_detail_backup,
 * detail-title-match, alias-hallucination, title-excludes-substring, condition
 * drift): v2 has no vendor URLs/SKUs to probe, so those checks no longer apply.
 */

export interface ValidateV2Result {
  ok: boolean;
  errors: string[];
  warnings: string[];
  /** Plain-English copies of `warnings` for the save UI (parity with v1). */
  userWarnings: string[];
  yamlText?: string;
  slug?: string;
}

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

// Canonical key order for the committed YAML (matches the ProfileV2 field order).
const V2_KEY_ORDER = [
  'schema_version',
  'slug',
  'display_name',
  'description',
  'product_type',
  'target',
  'queries',
  'match',
  'filters',
  'flags',
  'sources',
  'vendor_allowlist',
  'vendor_blocklist',
  'display',
  'schedule',
  'alerts',
];

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

/**
 * A carry-gate alias must be DISTINCTIVE — contain a digit OR be a multi-word
 * phrase. A bare generic word would match a vendor's whole catalog and defeat
 * the carry-gate (mirrors `MatchSpec.aliases_must_be_distinctive`, ADR-099).
 */
export function aliasIsDistinctive(a: string): boolean {
  const s = a.trim();
  const hasDigit = /[0-9]/.test(s);
  const isMultiword = s.split(/\s+/).filter(Boolean).length >= 2;
  return hasDigit || isMultiword;
}

/** Render a validated v2 draft to canonical `schema_version: 2` YAML. */
export function renderProfileV2Yaml(draft: Record<string, unknown>, slug?: string): string {
  const obj: Record<string, unknown> = { ...draft };
  obj.schema_version = 2;
  if (slug) obj.slug = slug;
  if (obj.target === undefined || obj.target === null) {
    obj.target = { unit: 'count', amount: 1 };
  }

  const out: Record<string, unknown> = {};
  for (const k of V2_KEY_ORDER) {
    const v = obj[k];
    if (v === undefined || v === null) continue;
    // Drop empty optional arrays for a clean file; `queries` is always kept.
    if (Array.isArray(v) && v.length === 0 && k !== 'queries') continue;
    out[k] = v;
  }
  // Append any non-canonical keys the LLM may have added (Pydantic ignores them).
  // Canonical keys are skipped here — the loop above already handled them,
  // including intentionally dropping empty optional arrays, which this fallback
  // must not resurrect.
  const canonical = new Set(V2_KEY_ORDER);
  for (const k of Object.keys(obj)) {
    if (canonical.has(k)) continue;
    if (!(k in out) && obj[k] !== undefined && obj[k] !== null) out[k] = obj[k];
  }
  return yaml.dump(out, { indent: 2, lineWidth: 120, noRefs: true });
}

/**
 * Validate a v2 onboarder draft. Returns blocking `errors` (save is rejected)
 * and non-blocking `warnings`. On success, `yamlText` is the file to commit.
 */
export function validateProfileDraftV2(
  draft: Record<string, unknown>,
  originalSlug?: string | null,
): ValidateV2Result {
  const errors: string[] = [];
  const warnings: string[] = [];

  if (draft.schema_version !== undefined && draft.schema_version !== 2) {
    errors.push('schema_version must be 2 for a v2 profile.');
  }

  // slug — pin to the URL slug in edit mode (parity with the v1 save route).
  let slug = typeof draft.slug === 'string' ? draft.slug.trim() : '';
  if (originalSlug && SLUG_RE.test(originalSlug)) slug = originalSlug;
  if (!slug) {
    errors.push('slug is required.');
  } else if (!SLUG_RE.test(slug)) {
    errors.push(
      `slug ${JSON.stringify(slug)} must match ${SLUG_RE.source} (lowercase letters, digits, hyphens).`,
    );
  }

  if (typeof draft.display_name !== 'string' || !draft.display_name.trim()) {
    errors.push('display_name is required.');
  }

  const queries = draft.queries;
  if (!Array.isArray(queries) || queries.length === 0) {
    errors.push(
      'queries is required — at least one search query (a profile with no query has nothing to search).',
    );
  } else if (queries.some((q) => typeof q !== 'string' || !q.trim())) {
    errors.push('each entry in queries must be a non-empty string.');
  }

  if (draft.target !== undefined && !isPlainObject(draft.target)) {
    errors.push('target must be an object like { unit: count, amount: 1 }.');
  }

  const match = isPlainObject(draft.match) ? draft.match : undefined;
  if (match && match.aliases !== undefined) {
    if (!Array.isArray(match.aliases)) {
      errors.push('match.aliases must be a list of strings.');
    } else {
      for (const a of match.aliases) {
        if (typeof a !== 'string' || !a.trim()) {
          errors.push('match.aliases: each entry must be a non-empty string.');
        } else if (!aliasIsDistinctive(a)) {
          errors.push(
            `match.aliases entry ${JSON.stringify(a)} is too generic — a single word with no ` +
              `digit would match a vendor's whole catalog and defeat the carry-gate (ADR-099). ` +
              `Use the model number, a SKU form, or a multi-word phrase.`,
          );
        }
      }
    }
  }

  const sources = isPlainObject(draft.sources) ? draft.sources : undefined;
  if (sources) {
    const serperEnabled = isPlainObject(sources.serper) ? sources.serper.enabled !== false : true;
    const ebayEnabled = isPlainObject(sources.ebay) ? sources.ebay.enabled === true : false;
    const amazonEnabled = isPlainObject(sources.amazon) ? sources.amazon.enabled === true : false;
    if (!serperEnabled && !ebayEnabled && !amazonEnabled) {
      errors.push('At least one source must be enabled (sources.serper, sources.ebay, or sources.amazon).');
    }
  }

  if (typeof draft.product_type !== 'string' || !draft.product_type.trim()) {
    warnings.push(
      'product_type is not set — type-aware display columns and default flags fall back to generic.',
    );
  }

  if (errors.length > 0) {
    return {
      ok: false,
      errors,
      warnings,
      userWarnings: [...warnings],
      slug: slug || undefined,
    };
  }

  let yamlText: string;
  try {
    yamlText = renderProfileV2Yaml(draft, slug);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'render failed';
    return {
      ok: false,
      errors: [`failed to render YAML from draft: ${msg}`],
      warnings,
      userWarnings: [...warnings],
      slug: slug || undefined,
    };
  }

  return { ok: true, errors, warnings, userWarnings: [...warnings], yamlText, slug };
}
