// Surgical mutator + reader for the `schedule:` block in profile.yaml.
// Mirror of the Schedule model in `worker/src/product_search/profile.py`.
// Pattern intentionally follows `web/lib/report-columns.ts`.
//
// Canonical block form (matches all committed profiles):
//
//   schedule:
//     cron: 0 8 * * *
//     timezone: UTC
//
// Schedule is optional: a profile without a `schedule:` block is run only
// via Run-now. Clearing the schedule from the UI removes the block.

export interface ScheduleConfig {
  cron: string;
}

export interface SchedulePreset {
  id: string;
  label: string;
  cron: string | null;
}

export const SCHEDULE_PRESETS: SchedulePreset[] = [
  { id: 'none', label: 'No schedule (Run now only)', cron: null },
  { id: 'daily', label: 'Daily at 08:00 UTC', cron: '0 8 * * *' },
  { id: 'hourly', label: 'Hourly', cron: '0 * * * *' },
  { id: 'every6h', label: 'Every 6 hours', cron: '0 */6 * * *' },
  { id: 'every12h', label: 'Every 12 hours', cron: '0 */12 * * *' },
];

const SCHEDULE_BLOCK_RE = /^schedule:[ \t]*\r?\n(?:[ \t]+[^\r\n]*\r?\n?)+/m;

export function readScheduleFromYaml(yamlText: string): ScheduleConfig | null {
  const match = SCHEDULE_BLOCK_RE.exec(yamlText);
  if (!match) return null;
  const cronLine = match[0].match(/^[ \t]+cron:[ \t]*(.+?)[ \t]*\r?$/m);
  if (!cronLine) return null;
  const cron = cronLine[1].replace(/^["']|["']$/g, '').trim();
  if (!cron) return null;
  return { cron };
}

export function applyScheduleToYaml(
  yamlText: string,
  schedule: ScheduleConfig | null
): string {
  const newBlock = schedule
    ? `schedule:\n  cron: ${schedule.cron}\n  timezone: UTC`
    : null;

  if (SCHEDULE_BLOCK_RE.test(yamlText)) {
    if (newBlock === null) {
      // Strip the block plus any single trailing blank line so we don't
      // leave double-blanks behind.
      return yamlText.replace(
        new RegExp(SCHEDULE_BLOCK_RE.source + '\\r?\\n?', 'm'),
        ''
      );
    }
    return yamlText.replace(SCHEDULE_BLOCK_RE, newBlock + '\n');
  }

  if (newBlock === null) return yamlText;
  return yamlText.replace(/\s*$/, '') + '\n\n' + newBlock + '\n';
}

/** Validate a 5-field cron expression. Mirror of the Python validator in
 *  `worker/src/product_search/profile.py::Schedule.cron_must_be_valid`.
 *  Returns null on valid, an error string on invalid. */
export function validateCron(cron: string): string | null {
  const trimmed = cron.trim();
  if (!trimmed) return 'cron expression is empty';
  const fields = trimmed.split(/\s+/);
  if (fields.length !== 5) {
    return `cron must have 5 space-separated fields, got ${fields.length}`;
  }
  // Allow digits, *, /, -, , for each field. Don't try to validate ranges
  // here — the worker-side validator does that on save.
  const ok = /^[0-9*/,\-]+$/;
  for (let i = 0; i < fields.length; i++) {
    if (!ok.test(fields[i])) {
      return `field ${i + 1} (${JSON.stringify(fields[i])}) contains invalid characters`;
    }
  }
  return null;
}

/** Identify the preset (if any) that matches the given cron. Falls back
 *  to "custom" when no preset matches — UI then shows the raw cron string. */
export function detectPreset(schedule: ScheduleConfig | null): string {
  if (!schedule) return 'none';
  const match = SCHEDULE_PRESETS.find((p) => p.cron === schedule.cron);
  return match ? match.id : 'custom';
}

/** Parse a single cron field into the set of values it matches within
 *  [min, max]. Supports `*`, `*\/N` (step), integer literals, and
 *  comma-separated lists. Returns null on any unsupported pattern. */
function expandCronField(field: string, min: number, max: number): number[] | null {
  const values = new Set<number>();
  for (const part of field.split(',')) {
    const step = part.match(/^(\*|\d+)(?:\/(\d+))?$/);
    if (!step) return null;
    const start = step[1] === '*' ? min : parseInt(step[1], 10);
    const stride = step[2] ? parseInt(step[2], 10) : 1;
    if (Number.isNaN(start) || Number.isNaN(stride) || stride < 1) return null;
    if (step[1] === '*' || step[2]) {
      for (let v = start; v <= max; v += stride) values.add(v);
    } else {
      if (start < min || start > max) return null;
      values.add(start);
    }
  }
  return [...values].sort((a, b) => a - b);
}

/** Compute the next UTC `Date` at which the cron expression fires, starting
 *  strictly after `now`. Returns null when the cron uses features not
 *  supported by `expandCronField` (e.g. ranges, day-of-week names). */
export function nextCronTick(cron: string, now: Date = new Date()): Date | null {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const minutes = expandCronField(fields[0], 0, 59);
  const hours = expandCronField(fields[1], 0, 23);
  const doms = expandCronField(fields[2], 1, 31);
  const months = expandCronField(fields[3], 1, 12);
  const dows = expandCronField(fields[4], 0, 6);
  if (!minutes || !hours || !doms || !months || !dows) return null;

  const minSet = new Set(minutes);
  const hourSet = new Set(hours);
  const domSet = new Set(doms);
  const monthSet = new Set(months);
  const dowSet = new Set(dows);

  // Walk minute-by-minute from `now + 1 minute`. Bounded to one year out;
  // any cron that doesn't fire within a year was already unsupported.
  const probe = new Date(now);
  probe.setUTCSeconds(0, 0);
  probe.setUTCMinutes(probe.getUTCMinutes() + 1);
  for (let i = 0; i < 366 * 24 * 60; i++) {
    if (
      minSet.has(probe.getUTCMinutes()) &&
      hourSet.has(probe.getUTCHours()) &&
      domSet.has(probe.getUTCDate()) &&
      monthSet.has(probe.getUTCMonth() + 1) &&
      dowSet.has(probe.getUTCDay())
    ) {
      return new Date(probe);
    }
    probe.setUTCMinutes(probe.getUTCMinutes() + 1);
  }
  return null;
}
