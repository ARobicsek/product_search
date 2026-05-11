// Surgical mutator + reader for the `alerts:` block in profile.yaml.
// Mirror of the AlertRule discriminated union in
// `worker/src/product_search/profile.py`. Pattern intentionally follows
// `web/lib/schedule.ts` so the schedule editor can layer alerts onto the same
// save flow.
//
// Canonical block form (matches what js-yaml dumps from `profile.alerts`):
//
//   alerts:
//     - kind: price_below
//       threshold_usd: 99.99
//     - kind: price_below
//       threshold_usd: 50
//       condition: new
//     - kind: vendor_seen
//       host: amazon.com
//
// Alerts are optional and default to []. An empty list is written as `alerts: []`
// inline; an undefined list omits the block entirely.

export interface PriceBelowRule {
  kind: 'price_below';
  threshold_usd: number;
  condition?: 'new' | 'used' | 'refurbished';
}

export interface VendorSeenRule {
  kind: 'vendor_seen';
  host: string;
}

export type AlertRule = PriceBelowRule | VendorSeenRule;

export const ALERT_CONDITIONS: ReadonlyArray<'new' | 'used' | 'refurbished'> = [
  'new',
  'used',
  'refurbished',
];

// Multi-line block list. Captures every indented sub-line until the next
// top-level key (a line that does not start with whitespace).
const ALERTS_BLOCK_RE = /^alerts:[ \t]*\r?\n((?:[ \t]+[^\r\n]*\r?\n?)+)/m;
// Inline empty list: `alerts: []`. js-yaml dumps the empty default this way.
const ALERTS_INLINE_EMPTY_RE = /^alerts:[ \t]*\[[ \t]*\][ \t]*\r?$/m;

function dedent(line: string): string {
  return line.replace(/^[ \t]+/, '');
}

export function readAlertsFromYaml(yamlText: string): AlertRule[] {
  if (ALERTS_INLINE_EMPTY_RE.test(yamlText)) return [];
  const match = ALERTS_BLOCK_RE.exec(yamlText);
  if (!match) return [];

  // Split the captured body into per-rule groups. Each rule starts with a
  // line whose first non-whitespace character is `-`.
  const rules: AlertRule[] = [];
  let current: Record<string, string> | null = null;

  const lines = match[1].split(/\r?\n/);
  for (const raw of lines) {
    if (!raw.trim()) continue;
    const stripped = dedent(raw);
    if (stripped.startsWith('-')) {
      if (current) {
        const parsed = parseRuleFields(current);
        if (parsed) rules.push(parsed);
      }
      current = {};
      // The first key/value can live on the same line as the dash.
      const inline = stripped.slice(1).trim();
      if (inline) {
        const kv = splitKeyValue(inline);
        if (kv) current[kv.key] = kv.value;
      }
    } else if (current) {
      const kv = splitKeyValue(stripped);
      if (kv) current[kv.key] = kv.value;
    }
  }
  if (current) {
    const parsed = parseRuleFields(current);
    if (parsed) rules.push(parsed);
  }
  return rules;
}

function splitKeyValue(line: string): { key: string; value: string } | null {
  const idx = line.indexOf(':');
  if (idx < 0) return null;
  const key = line.slice(0, idx).trim();
  const value = line.slice(idx + 1).trim().replace(/^["']|["']$/g, '');
  if (!key) return null;
  return { key, value };
}

function parseRuleFields(fields: Record<string, string>): AlertRule | null {
  const kind = fields.kind;
  if (kind === 'price_below') {
    const threshold = Number(fields.threshold_usd);
    if (!Number.isFinite(threshold) || !(threshold > 0)) return null;
    const out: PriceBelowRule = { kind: 'price_below', threshold_usd: threshold };
    const cond = fields.condition;
    if (cond && (ALERT_CONDITIONS as ReadonlyArray<string>).includes(cond)) {
      out.condition = cond as PriceBelowRule['condition'];
    }
    return out;
  }
  if (kind === 'vendor_seen') {
    const host = (fields.host ?? '').trim();
    if (!host) return null;
    return { kind: 'vendor_seen', host };
  }
  return null;
}

function renderRule(rule: AlertRule): string {
  if (rule.kind === 'price_below') {
    const lines = [
      `  - kind: price_below`,
      `    threshold_usd: ${rule.threshold_usd}`,
    ];
    if (rule.condition) lines.push(`    condition: ${rule.condition}`);
    return lines.join('\n');
  }
  return [`  - kind: vendor_seen`, `    host: ${rule.host}`].join('\n');
}

export function applyAlertsToYaml(yamlText: string, rules: AlertRule[]): string {
  const newBlock =
    rules.length === 0
      ? 'alerts: []'
      : ['alerts:', ...rules.map(renderRule)].join('\n');

  if (ALERTS_BLOCK_RE.test(yamlText)) {
    return yamlText.replace(ALERTS_BLOCK_RE, newBlock + '\n');
  }
  if (ALERTS_INLINE_EMPTY_RE.test(yamlText)) {
    return yamlText.replace(ALERTS_INLINE_EMPTY_RE, newBlock);
  }

  // Block is absent. Insert before `schedule:` if present (canonical
  // ordering in render-yaml.ts puts alerts right before schedule), else
  // append at end of file. Skip writing an empty-list block when none
  // existed before — keeps untouched YAML stable.
  if (rules.length === 0) return yamlText;
  const schedRe = /^schedule:/m;
  if (schedRe.test(yamlText)) {
    return yamlText.replace(schedRe, `${newBlock}\n\n$&`);
  }
  return yamlText.replace(/\s*$/, '') + '\n\n' + newBlock + '\n';
}

/** Validate one alert rule. Returns null on valid, an error string on invalid.
 *  Mirrors the Pydantic constraints in profile.py. */
export function validateAlertRule(rule: AlertRule): string | null {
  if (rule.kind === 'price_below') {
    if (!Number.isFinite(rule.threshold_usd) || !(rule.threshold_usd > 0)) {
      return 'threshold must be a positive number';
    }
    if (
      rule.condition !== undefined &&
      !(ALERT_CONDITIONS as ReadonlyArray<string>).includes(rule.condition)
    ) {
      return `condition must be one of ${ALERT_CONDITIONS.join(', ')}`;
    }
    return null;
  }
  if (rule.kind === 'vendor_seen') {
    if (!rule.host || !rule.host.trim()) return 'host is required';
    // Reject obvious not-a-host inputs. The worker canonicalizes to
    // www-stripped lowercase, so we just sanity-check the shape here.
    if (!/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(rule.host.trim())) {
      return 'host must look like a domain (e.g. amazon.com)';
    }
    return null;
  }
  return 'unknown alert kind';
}

/** Human-readable one-line summary of a rule, used by the UI row and the
 *  worker-side audit panel headline (mirrored in `alerts.py::FiredAlert`). */
export function describeRule(rule: AlertRule): string {
  if (rule.kind === 'price_below') {
    const cond = rule.condition ? ` (${rule.condition} only)` : '';
    return `Cheapest${cond} drops below $${rule.threshold_usd.toLocaleString()}`;
  }
  return `Any listing seen at ${rule.host}`;
}
