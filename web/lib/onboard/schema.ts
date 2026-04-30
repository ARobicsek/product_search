import 'server-only';
import yaml from 'js-yaml';

// Mirrors worker/src/product_search/profile.py. The Python validator runs in
// CI on every commit that touches products/, so this TS check is best-effort
// defence-in-depth: catch obvious mistakes before the commit lands so the
// user sees the error in the onboarding UI rather than as a CI failure five
// minutes later. If this drifts from the Pydantic model, CI will catch it.

export const KNOWN_SOURCE_IDS = new Set<string>([
  'ebay_search',
  'nemixram_storefront',
  'cloudstoragecorp_ebay',
  'memstore_ebay',
  'newegg_search',
  'serversupply_search',
  'memorynet_search',
  'theserverstore_storefront',
]);

export const KNOWN_FILTER_RULES = new Set<string>([
  'form_factor_in',
  'speed_mts_min',
  'ecc_required',
  'voltage_eq',
  'min_quantity_for_target',
  'in_stock',
  'single_sku_url',
  'title_excludes',
]);

export const KNOWN_FLAG_RULES = new Set<string>([
  'ship_from_country_in',
  'brand_in',
  'low_seller_feedback',
  'kingston_e_suffix',
  'title_mentions_other_server',
  'title_mentions',
]);

// Mirrors worker/src/product_search/profile.py:KNOWN_REPORT_COLUMNS.
// Keep in sync with worker/src/product_search/synthesizer/synthesizer.py:COLUMN_DEFS.
export const KNOWN_REPORT_COLUMNS = new Set<string>([
  'rank',
  'source',
  'title',
  'price_unit',
  'total_for_target',
  'qty',
  'condition',
  'brand',
  'mpn',
  'seller',
  'seller_rating',
  'ship_from',
  'qvl_status',
  'flags',
]);

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const CRON_FIELD_RE = /^[\d*/,\-]+$/;
const SPEC_TYPES = new Set(['int', 'str', 'float', 'bool']);

export interface ParsedProfile {
  slug: string;
  display_name: string;
  description: string;
  qvl_file: string;
  // ...rest of the fields are validated structurally but not exposed.
  [key: string]: unknown;
}

export class ProfileValidationError extends Error {
  readonly errors: string[];
  constructor(errors: string[]) {
    super(errors.join('; '));
    this.errors = errors;
    this.name = 'ProfileValidationError';
  }
}

interface ValidationContext {
  errors: string[];
}

function asObject(v: unknown, path: string, ctx: ValidationContext): Record<string, unknown> | null {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) {
    ctx.errors.push(`${path}: expected object`);
    return null;
  }
  return v as Record<string, unknown>;
}

function asArray(v: unknown, path: string, ctx: ValidationContext): unknown[] | null {
  if (!Array.isArray(v)) {
    ctx.errors.push(`${path}: expected array`);
    return null;
  }
  return v;
}

function asString(v: unknown, path: string, ctx: ValidationContext): string | null {
  if (typeof v !== 'string') {
    ctx.errors.push(`${path}: expected string`);
    return null;
  }
  return v;
}

function asInt(v: unknown, path: string, ctx: ValidationContext, opts: { gt?: number } = {}): number | null {
  if (typeof v !== 'number' || !Number.isInteger(v)) {
    ctx.errors.push(`${path}: expected integer`);
    return null;
  }
  if (opts.gt !== undefined && v <= opts.gt) {
    ctx.errors.push(`${path}: must be > ${opts.gt}`);
    return null;
  }
  return v;
}

function validateCron(cron: string, path: string, ctx: ValidationContext) {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) {
    ctx.errors.push(`${path}: cron must have exactly 5 fields, got ${fields.length}`);
    return;
  }
  for (const f of fields) {
    if (!CRON_FIELD_RE.test(f)) {
      ctx.errors.push(`${path}: invalid cron field ${JSON.stringify(f)}`);
      return;
    }
  }
}

function validateTarget(target: unknown, ctx: ValidationContext) {
  const t = asObject(target, 'target', ctx);
  if (!t) return;
  asString(t.unit, 'target.unit', ctx);
  asInt(t.amount, 'target.amount', ctx, { gt: 0 });
  const configs = asArray(t.configurations, 'target.configurations', ctx);
  if (!configs) return;
  if (configs.length < 1) ctx.errors.push('target.configurations: needs at least one entry');
  configs.forEach((c, i) => {
    const co = asObject(c, `target.configurations[${i}]`, ctx);
    if (!co) return;
    asInt(co.module_count, `target.configurations[${i}].module_count`, ctx, { gt: 0 });
    asInt(co.module_capacity_gb, `target.configurations[${i}].module_capacity_gb`, ctx, { gt: 0 });
  });
}

function validateSpecAttrs(specAttrs: unknown, ctx: ValidationContext) {
  const sa = asObject(specAttrs, 'spec_attrs', ctx);
  if (!sa) return;
  const keys = Object.keys(sa);
  if (keys.length < 1) ctx.errors.push('spec_attrs: needs at least one attribute');
  for (const k of keys) {
    const def = asObject(sa[k], `spec_attrs.${k}`, ctx);
    if (!def) continue;
    const t = asString(def.type, `spec_attrs.${k}.type`, ctx);
    if (t !== null && !SPEC_TYPES.has(t)) {
      ctx.errors.push(`spec_attrs.${k}.type: must be one of ${[...SPEC_TYPES].join(',')}`);
    }
    if (typeof def.required !== 'boolean') {
      ctx.errors.push(`spec_attrs.${k}.required: expected boolean`);
    }
    if (def.enum !== undefined && def.enum !== null) {
      const en = asArray(def.enum, `spec_attrs.${k}.enum`, ctx);
      if (en) en.forEach((e, i) => asString(e, `spec_attrs.${k}.enum[${i}]`, ctx));
    }
  }
}

function validateRules(
  list: unknown,
  path: string,
  knownRules: Set<string>,
  requireFlagKey: boolean,
  ctx: ValidationContext,
) {
  const arr = asArray(list, path, ctx);
  if (!arr) return;
  if (arr.length < 1) ctx.errors.push(`${path}: needs at least one entry`);
  arr.forEach((r, i) => {
    const ro = asObject(r, `${path}[${i}]`, ctx);
    if (!ro) return;
    const rule = asString(ro.rule, `${path}[${i}].rule`, ctx);
    if (rule !== null && !knownRules.has(rule)) {
      ctx.errors.push(
        `${path}[${i}].rule: unknown rule ${JSON.stringify(rule)}; known: ${[...knownRules].sort().join(',')}`,
      );
    }
    if (requireFlagKey) {
      asString(ro.flag, `${path}[${i}].flag`, ctx);
    }
  });
}

function validateSources(
  list: unknown,
  path: string,
  pendingAllowed: boolean,
  minLength: number,
  ctx: ValidationContext,
) {
  const arr = asArray(list, path, ctx);
  if (!arr) return;
  if (arr.length < minLength) ctx.errors.push(`${path}: needs at least ${minLength} entry(ies)`);
  arr.forEach((s, i) => {
    const so = asObject(s, `${path}[${i}]`, ctx);
    if (!so) return;
    const id = asString(so.id, `${path}[${i}].id`, ctx);
    if (id !== null && !KNOWN_SOURCE_IDS.has(id)) {
      ctx.errors.push(
        `${path}[${i}].id: unknown source id ${JSON.stringify(id)}; known: ${[...KNOWN_SOURCE_IDS].sort().join(',')}`,
      );
    }
    void pendingAllowed;
  });
}

function validateSchedule(schedule: unknown, ctx: ValidationContext) {
  const s = asObject(schedule, 'schedule', ctx);
  if (!s) return;
  const cron = asString(s.cron, 'schedule.cron', ctx);
  if (cron !== null) validateCron(cron, 'schedule.cron', ctx);
  if (s.timezone !== 'UTC') ctx.errors.push('schedule.timezone: must be exactly "UTC"');
}

export function parseAndValidateProfileYaml(text: string): ParsedProfile {
  let doc: unknown;
  try {
    doc = yaml.load(text, { schema: yaml.CORE_SCHEMA });
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'unknown YAML error';
    throw new ProfileValidationError([`yaml parse error: ${msg}`]);
  }

  const ctx: ValidationContext = { errors: [] };
  const obj = asObject(doc, '<root>', ctx);
  if (!obj) throw new ProfileValidationError(ctx.errors);

  const slug = asString(obj.slug, 'slug', ctx);
  if (slug !== null && !SLUG_RE.test(slug)) {
    ctx.errors.push(`slug: must match ${SLUG_RE.source}`);
  }
  asString(obj.display_name, 'display_name', ctx);
  asString(obj.description, 'description', ctx);

  validateTarget(obj.target, ctx);
  validateSpecAttrs(obj.spec_attrs, ctx);
  validateRules(obj.spec_filters, 'spec_filters', KNOWN_FILTER_RULES, false, ctx);
  validateRules(obj.spec_flags, 'spec_flags', KNOWN_FLAG_RULES, true, ctx);

  validateSources(obj.sources, 'sources', false, 1, ctx);
  if (obj.sources_pending !== undefined) {
    validateSources(obj.sources_pending, 'sources_pending', true, 0, ctx);
  }

  const qvlFile = asString(obj.qvl_file, 'qvl_file', ctx);
  if (slug !== null && qvlFile !== null && !qvlFile.includes(slug)) {
    ctx.errors.push(`qvl_file: must contain slug ${JSON.stringify(slug)}`);
  }

  if (obj.synthesis_hints !== undefined) {
    const hints = asArray(obj.synthesis_hints, 'synthesis_hints', ctx);
    if (hints) hints.forEach((h, i) => asString(h, `synthesis_hints[${i}]`, ctx));
  }

  if (obj.report_columns !== undefined && obj.report_columns !== null) {
    const cols = asArray(obj.report_columns, 'report_columns', ctx);
    if (cols) {
      if (cols.length < 1) {
        ctx.errors.push('report_columns: list must be non-empty if provided');
      }
      const seen = new Set<string>();
      cols.forEach((c, i) => {
        const id = asString(c, `report_columns[${i}]`, ctx);
        if (id === null) return;
        if (!KNOWN_REPORT_COLUMNS.has(id)) {
          ctx.errors.push(
            `report_columns[${i}]: unknown column ${JSON.stringify(id)}; known: ${[...KNOWN_REPORT_COLUMNS].sort().join(',')}`,
          );
        }
        if (seen.has(id)) {
          ctx.errors.push(`report_columns[${i}]: duplicate column ${JSON.stringify(id)}`);
        }
        seen.add(id);
      });
    }
  }

  validateSchedule(obj.schedule, ctx);

  if (ctx.errors.length > 0) {
    throw new ProfileValidationError(ctx.errors);
  }

  return obj as ParsedProfile;
}

export function extractLatestYamlBlock(markdown: string): string | null {
  // Find the last fenced ```yaml ... ``` block in a markdown string.
  const re = /```ya?ml\s*\n([\s\S]*?)```/gi;
  let last: string | null = null;
  let m: RegExpExecArray | null;
  while ((m = re.exec(markdown)) !== null) {
    last = m[1];
  }
  return last;
}
