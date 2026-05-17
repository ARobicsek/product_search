// Surgical mutator + reader + local-time helpers for the `schedule:` block
// in profile.yaml. Mirror of the Schedule model in
// `worker/src/product_search/profile.py`. Pattern intentionally follows
// `web/lib/report-columns.ts`.
//
// A schedule is EITHER recurring (a 5-field UTC cron) OR one-time (an
// absolute UTC instant `run_at`). Exactly one is set. `timezone` is always
// UTC — the user picks a wall-clock time in their own zone in the UI and we
// convert to UTC here, so the stored model never carries a non-UTC zone.
//
// Canonical block forms (what we render and what the worker reads):
//
//   schedule:                         schedule:
//     cron: 0 8 * * *                   run_at: 2026-05-17T12:30:00Z
//     timezone: UTC                     timezone: UTC
//
// Schedule is optional: a profile without a `schedule:` block runs only via
// Run-now. Clearing the schedule from the UI removes the block entirely.

export type ScheduleConfig =
  | { kind: 'recurring'; cron: string }
  | { kind: 'once'; runAtIso: string }; // ISO-8601 UTC, e.g. 2026-05-17T12:30:00Z

export type PresetId =
  | 'none'
  | 'once'
  | 'daily'
  | 'every6h'
  | 'every12h'
  | 'hourly'
  | 'custom';

export interface SchedulePreset {
  id: PresetId;
  label: string;
  /** Fixed cron for presets that need no time input. */
  cron?: string;
  /** Shows the time + timezone picker. */
  needsTime?: boolean;
  /** Shows the date picker (one-time only). */
  needsDate?: boolean;
}

export const SCHEDULE_PRESETS: SchedulePreset[] = [
  { id: 'none', label: 'No schedule (Run now only)' },
  { id: 'once', label: 'One time only', needsTime: true, needsDate: true },
  { id: 'daily', label: 'Every day', needsTime: true },
  { id: 'every6h', label: 'Every 6 hours', cron: '0 */6 * * *' },
  { id: 'every12h', label: 'Every 12 hours', cron: '0 */12 * * *' },
  { id: 'hourly', label: 'Hourly', cron: '0 * * * *' },
  { id: 'custom', label: 'Custom cron' },
];

// Time pickers snap to 15-minute increments. The scheduler heartbeat ticks
// every 15 min, so finer precision wouldn't be honoured anyway.
export const TIME_STEP_SECONDS = 900;

// ---------------------------------------------------------------------------
// YAML read / write (regex-surgical, like report-columns.ts)
// ---------------------------------------------------------------------------

const SCHEDULE_BLOCK_RE = /^schedule:[ \t]*\r?\n(?:[ \t]+[^\r\n]*\r?\n?)+/m;

export function readScheduleFromYaml(yamlText: string): ScheduleConfig | null {
  const match = SCHEDULE_BLOCK_RE.exec(yamlText);
  if (!match) return null;
  const block = match[0];

  const runAtLine = block.match(/^[ \t]+run_at:[ \t]*(.+?)[ \t]*\r?$/m);
  if (runAtLine) {
    const raw = runAtLine[1].replace(/^["']|["']$/g, '').trim();
    if (!raw) return null;
    return { kind: 'once', runAtIso: raw };
  }

  const cronLine = block.match(/^[ \t]+cron:[ \t]*(.+?)[ \t]*\r?$/m);
  if (cronLine) {
    const cron = cronLine[1].replace(/^["']|["']$/g, '').trim();
    if (!cron) return null;
    return { kind: 'recurring', cron };
  }
  return null;
}

export function applyScheduleToYaml(
  yamlText: string,
  schedule: ScheduleConfig | null,
): string {
  let newBlock: string | null = null;
  if (schedule?.kind === 'recurring') {
    newBlock = `schedule:\n  cron: ${schedule.cron}\n  timezone: UTC`;
  } else if (schedule?.kind === 'once') {
    newBlock = `schedule:\n  run_at: ${schedule.runAtIso}\n  timezone: UTC`;
  }

  if (SCHEDULE_BLOCK_RE.test(yamlText)) {
    if (newBlock === null) {
      // Strip the block plus any single trailing blank line so we don't
      // leave double-blanks behind.
      return yamlText.replace(
        new RegExp(SCHEDULE_BLOCK_RE.source + '\\r?\\n?', 'm'),
        '',
      );
    }
    return yamlText.replace(SCHEDULE_BLOCK_RE, newBlock + '\n');
  }

  if (newBlock === null) return yamlText;
  return yamlText.replace(/\s*$/, '') + '\n\n' + newBlock + '\n';
}

// ---------------------------------------------------------------------------
// Cron validation (unchanged contract — mirror of profile.py)
// ---------------------------------------------------------------------------

/** Validate a 5-field cron expression. Returns null on valid, an error
 *  string on invalid. */
export function validateCron(cron: string): string | null {
  const trimmed = cron.trim();
  if (!trimmed) return 'cron expression is empty';
  const fields = trimmed.split(/\s+/);
  if (fields.length !== 5) {
    return `cron must have 5 space-separated fields, got ${fields.length}`;
  }
  const ok = /^[0-9*/,\-]+$/;
  for (let i = 0; i < fields.length; i++) {
    if (!ok.test(fields[i])) {
      return `field ${i + 1} (${JSON.stringify(fields[i])}) contains invalid characters`;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Timezone handling — pick a wall-clock time locally, store UTC.
// ---------------------------------------------------------------------------

export interface TzOption {
  id: string;
  label: string;
}

export function detectBrowserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** Common US zones + UTC, with the browser's own zone surfaced first. */
export function buildTimezoneOptions(): TzOption[] {
  const browser = detectBrowserTimeZone();
  const base: TzOption[] = [
    { id: 'America/New_York', label: 'Eastern (ET)' },
    { id: 'America/Chicago', label: 'Central (CT)' },
    { id: 'America/Denver', label: 'Mountain (MT)' },
    { id: 'America/Los_Angeles', label: 'Pacific (PT)' },
    { id: 'UTC', label: 'UTC' },
  ];
  const idx = base.findIndex((b) => b.id === browser);
  if (idx === -1) {
    base.unshift({ id: browser, label: `Your time (${browser})` });
  } else {
    const [b] = base.splice(idx, 1);
    base.unshift({ id: b.id, label: `${b.label} — your time` });
  }
  return base;
}

/** Milliseconds to ADD to a UTC clock to express it as wall time in
 *  `timeZone` at the given instant (i.e. wall − utc). */
function tzOffsetMs(timeZone: string, date: Date): number {
  const dtf = new Intl.DateTimeFormat('en-US', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
  const parts = dtf.formatToParts(date);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
  const asUTC = Date.UTC(
    get('year'),
    get('month') - 1,
    get('day'),
    get('hour'),
    get('minute'),
    get('second'),
  );
  return asUTC - date.getTime();
}

/** Interpret (y, mo[1-12], d, h, mi) as a wall-clock time in `timeZone` and
 *  return the corresponding UTC Date. Two-pass to stay correct across DST
 *  transitions (except inside the 1h spring-forward gap, which has no valid
 *  instant — an accepted edge). */
export function zonedWallTimeToUtc(
  y: number,
  mo: number,
  d: number,
  h: number,
  mi: number,
  timeZone: string,
): Date {
  const guess = Date.UTC(y, mo - 1, d, h, mi, 0);
  const off1 = tzOffsetMs(timeZone, new Date(guess));
  let utc = guess - off1;
  const off2 = tzOffsetMs(timeZone, new Date(utc));
  if (off2 !== off1) utc = guess - off2;
  return new Date(utc);
}

/** Calendar Y/M/D and HH:MM of an instant, as seen in `timeZone`. */
function zonedParts(iso: string | Date, timeZone: string) {
  const date = typeof iso === 'string' ? new Date(iso) : iso;
  const dtf = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    hourCycle: 'h23',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
  const p = dtf.formatToParts(date);
  const get = (t: string) => p.find((x) => x.type === t)?.value ?? '';
  return {
    date: `${get('year')}-${get('month')}-${get('day')}`,
    time: `${get('hour')}:${get('minute')}`,
  };
}

// ---------------------------------------------------------------------------
// Preset <-> ScheduleConfig builders
// ---------------------------------------------------------------------------

/** Today's calendar date (YYYY-MM-DD) in the given zone. */
export function todayInZone(timeZone: string): string {
  return zonedParts(new Date(), timeZone).date;
}

/** "Every day at HH:MM <tz>" → a daily UTC cron `M H * * *`. Uses today's
 *  date in `timeZone` for the offset, so the stored cron can drift by 1h
 *  across a DST transition (a documented, accepted tradeoff). */
export function dailyLocalToCron(hhmm: string, timeZone: string): string {
  const [h, mi] = hhmm.split(':').map(Number);
  const today = todayInZone(timeZone);
  const [y, mo, d] = today.split('-').map(Number);
  const utc = zonedWallTimeToUtc(y, mo, d, h, mi, timeZone);
  return `${utc.getUTCMinutes()} ${utc.getUTCHours()} * * *`;
}

/** One-time at <date> <HH:MM> <tz> → ISO-8601 UTC instant (no millis). */
export function onceLocalToIso(
  dateStr: string,
  hhmm: string,
  timeZone: string,
): string {
  const [y, mo, d] = dateStr.split('-').map(Number);
  const [h, mi] = hhmm.split(':').map(Number);
  const utc = zonedWallTimeToUtc(y, mo, d, h, mi, timeZone);
  return utc.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

const DAILY_CRON_RE = /^(\d{1,2}) (\d{1,2}) \* \* \*$/;

/** If `cron` is a plain "daily at M H UTC", return its local HH:MM in
 *  `timeZone`; else null (used to pre-fill the picker when editing). */
export function dailyCronToLocalHHMM(
  cron: string,
  timeZone: string,
): string | null {
  const m = cron.trim().match(DAILY_CRON_RE);
  if (!m) return null;
  const minute = Number(m[1]);
  const hour = Number(m[2]);
  if (minute > 59 || hour > 23) return null;
  // Anchor on today's UTC date; only the time-of-day matters for display.
  const now = new Date();
  const utc = new Date(
    Date.UTC(
      now.getUTCFullYear(),
      now.getUTCMonth(),
      now.getUTCDate(),
      hour,
      minute,
    ),
  );
  return zonedParts(utc, timeZone).time;
}

/** Local date + time of a one-time instant, for pre-filling the editor. */
export function isoToLocalParts(
  runAtIso: string,
  timeZone: string,
): { date: string; time: string } {
  return zonedParts(runAtIso, timeZone);
}

/** Identify which preset a stored schedule corresponds to. */
export function detectPreset(schedule: ScheduleConfig | null): PresetId {
  if (!schedule) return 'none';
  if (schedule.kind === 'once') return 'once';
  const fixed = SCHEDULE_PRESETS.find(
    (p) => p.cron && p.cron === schedule.cron,
  );
  if (fixed) return fixed.id;
  if (DAILY_CRON_RE.test(schedule.cron.trim())) return 'daily';
  return 'custom';
}

// ---------------------------------------------------------------------------
// Next-run computation
// ---------------------------------------------------------------------------

/** Parse a single cron field into the set of values it matches within
 *  [min, max]. Supports `*`, `*\/N` (step), integer literals, and
 *  comma-separated lists. Returns null on any unsupported pattern. */
function expandCronField(
  field: string,
  min: number,
  max: number,
): number[] | null {
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
 *  supported by `expandCronField`. */
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

/** The next time this schedule will run (UTC Date), or null if it has no
 *  future run (cleared, unparseable cron, or a one-time instant already in
 *  the past). */
export function nextRunDate(
  schedule: ScheduleConfig | null,
  now: Date = new Date(),
): Date | null {
  if (!schedule) return null;
  if (schedule.kind === 'once') {
    const d = new Date(schedule.runAtIso);
    if (Number.isNaN(d.getTime())) return null;
    return d.getTime() > now.getTime() ? d : null;
  }
  return nextCronTick(schedule.cron, now);
}
