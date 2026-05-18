'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Bell, CalendarClock, CheckCircle2, Loader2, Pencil, Plus, Trash2, X } from 'lucide-react';
import {
  applyScheduleToYaml,
  buildTimezoneOptions,
  dailyCronToLocalHHMM,
  dailyLocalToCron,
  detectBrowserTimeZone,
  detectPreset,
  isoToLocalParts,
  nextRunDate,
  onceLocalToIso,
  readScheduleFromYaml,
  SCHEDULE_PRESETS,
  TIME_STEP_SECONDS,
  todayInZone,
  validateCron,
  type PresetId,
  type ScheduleConfig,
  type TzOption,
} from '@/lib/schedule';
import {
  applyAlertsToYaml,
  describeRule,
  readAlertsFromYaml,
  validateAlertRule,
  type AlertRule,
} from '@/lib/alerts';

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'success' }
  | { kind: 'error'; message: string; details?: string[] };

type AlertDraft =
  | { kind: 'price_below'; threshold_usd: string; condition: '' | 'new' | 'used' | 'refurbished' }
  | { kind: 'vendor_seen'; host: string };

function ruleToDraft(rule: AlertRule): AlertDraft {
  if (rule.kind === 'price_below') {
    return {
      kind: 'price_below',
      threshold_usd: String(rule.threshold_usd),
      condition: rule.condition ?? '',
    };
  }
  return { kind: 'vendor_seen', host: rule.host };
}

function draftToRule(draft: AlertDraft): { rule: AlertRule | null; error: string | null } {
  if (draft.kind === 'price_below') {
    const threshold = Number(draft.threshold_usd);
    if (!Number.isFinite(threshold) || !(threshold > 0)) {
      return { rule: null, error: 'threshold must be a positive number' };
    }
    const rule: AlertRule = { kind: 'price_below', threshold_usd: threshold };
    if (draft.condition) rule.condition = draft.condition;
    const err = validateAlertRule(rule);
    return err ? { rule: null, error: err } : { rule, error: null };
  }
  const host = draft.host.trim().toLowerCase().replace(/^www\./, '');
  const rule: AlertRule = { kind: 'vendor_seen', host };
  const err = validateAlertRule(rule);
  return err ? { rule: null, error: err } : { rule, error: null };
}

function describeSchedule(schedule: ScheduleConfig | null): string {
  if (!schedule) return 'Runs only when you click Run now.';
  if (schedule.kind === 'once') {
    const d = new Date(schedule.runAtIso);
    if (Number.isNaN(d.getTime())) return 'One-time run.';
    return `One-time run at ${d.toLocaleString()} (your time).`;
  }
  return `Recurring (cron ${schedule.cron}, UTC).`;
}

export function ScheduleEditorButton({
  profileYaml,
}: {
  product: string;
  profileYaml: string | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle' });

  const initialAlerts = profileYaml ? readAlertsFromYaml(profileYaml) : [];

  // Schedule picker state. Real values are computed in openEditor() (on the
  // client, after a user click) to avoid any SSR/hydration timezone mismatch.
  const [presetId, setPresetId] = useState<PresetId>('none');
  const [customCron, setCustomCron] = useState<string>('');
  const [timeHHMM, setTimeHHMM] = useState<string>('08:00');
  const [dateStr, setDateStr] = useState<string>('');
  const [tz, setTz] = useState<string>('UTC');
  const [tzOptions, setTzOptions] = useState<TzOption[]>([]);
  const [initialScheduleConfig, setInitialScheduleConfig] =
    useState<ScheduleConfig | null>(null);
  // Clock captured when the editor opens (render must stay pure — no
  // Date.now() during render). Used to flag a one-time run set in the past.
  const [openedAtMs, setOpenedAtMs] = useState<number>(0);

  const [alerts, setAlerts] = useState<AlertRule[]>(initialAlerts);
  // Editing index: number = editing that row, -1 = adding new, null = not editing.
  const [editIdx, setEditIdx] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<AlertDraft>({
    kind: 'price_below',
    threshold_usd: '',
    condition: '',
  });
  const [editError, setEditError] = useState<string | null>(null);
  const [pushSubscribed, setPushSubscribed] = useState<boolean | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  // Probe local push subscription whenever the popover opens. Mirrors the
  // logic in AlertsBell — we only care about *this* device's state.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        if (!cancelled) setPushSubscribed(false);
        return;
      }
      try {
        const registration = await navigator.serviceWorker.ready;
        const sub = await registration.pushManager.getSubscription();
        if (!cancelled) setPushSubscribed(!!sub);
      } catch {
        if (!cancelled) setPushSubscribed(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  function openEditor() {
    const fresh = profileYaml ? readScheduleFromYaml(profileYaml) : null;
    const zone = detectBrowserTimeZone();
    const today = todayInZone(zone);
    const p = detectPreset(fresh);

    setTz(zone);
    setTzOptions(buildTimezoneOptions());
    setPresetId(p);
    setCustomCron(
      p === 'custom' && fresh?.kind === 'recurring' ? fresh.cron : '',
    );

    if (fresh?.kind === 'once') {
      const parts = isoToLocalParts(fresh.runAtIso, zone);
      setDateStr(parts.date);
      setTimeHHMM(parts.time);
    } else if (fresh?.kind === 'recurring' && p === 'daily') {
      setDateStr(today);
      setTimeHHMM(dailyCronToLocalHHMM(fresh.cron, zone) ?? '08:00');
    } else {
      setDateStr(today);
      setTimeHHMM('08:00');
    }

    setInitialScheduleConfig(fresh);
    setOpenedAtMs(Date.now());
    setAlerts(profileYaml ? readAlertsFromYaml(profileYaml) : []);
    setEditIdx(null);
    setEditError(null);
    setSaveState({ kind: 'idle' });
    setOpen(true);
  }

  function resolveSchedule(): { schedule: ScheduleConfig | null; error: string | null } {
    if (presetId === 'none') return { schedule: null, error: null };
    if (presetId === 'custom') {
      const err = validateCron(customCron);
      if (err) return { schedule: null, error: err };
      return { schedule: { kind: 'recurring', cron: customCron.trim() }, error: null };
    }
    if (presetId === 'once') {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
        return { schedule: null, error: 'pick a date' };
      }
      if (!/^\d{1,2}:\d{2}$/.test(timeHHMM)) {
        return { schedule: null, error: 'pick a time' };
      }
      return {
        schedule: { kind: 'once', runAtIso: onceLocalToIso(dateStr, timeHHMM, tz) },
        error: null,
      };
    }
    if (presetId === 'daily') {
      if (!/^\d{1,2}:\d{2}$/.test(timeHHMM)) {
        return { schedule: null, error: 'pick a time' };
      }
      return {
        schedule: { kind: 'recurring', cron: dailyLocalToCron(timeHHMM, tz) },
        error: null,
      };
    }
    const preset = SCHEDULE_PRESETS.find((p) => p.id === presetId);
    if (!preset || !preset.cron) return { schedule: null, error: 'invalid preset' };
    return { schedule: { kind: 'recurring', cron: preset.cron }, error: null };
  }

  const resolved = resolveSchedule();
  const localValidationError = resolved.error;
  const nextRun = localValidationError
    ? null
    : nextRunDate(resolved.schedule, new Date(openedAtMs));
  const onceInPast =
    resolved.schedule?.kind === 'once' &&
    new Date(resolved.schedule.runAtIso).getTime() <= openedAtMs;

  const initialPayload = JSON.stringify({
    schedule: initialScheduleConfig,
    alerts: initialAlerts,
  });
  const currentPayload = JSON.stringify({ schedule: resolved.schedule, alerts });
  const dirty = initialPayload !== currentPayload;

  function startAdd() {
    setEditIdx(-1);
    setEditDraft({ kind: 'price_below', threshold_usd: '', condition: '' });
    setEditError(null);
  }

  function startEdit(i: number) {
    setEditIdx(i);
    setEditDraft(ruleToDraft(alerts[i]));
    setEditError(null);
  }

  function cancelEdit() {
    setEditIdx(null);
    setEditError(null);
  }

  function commitEdit() {
    const { rule, error } = draftToRule(editDraft);
    if (error || !rule) {
      setEditError(error ?? 'invalid rule');
      return;
    }
    setAlerts((prev) => {
      if (editIdx === null || editIdx < 0) return [...prev, rule];
      const out = prev.slice();
      out[editIdx] = rule;
      return out;
    });
    setEditIdx(null);
    setEditError(null);
  }

  function deleteRule(i: number) {
    setAlerts((prev) => prev.filter((_, j) => j !== i));
    if (editIdx === i) setEditIdx(null);
  }

  async function onSave() {
    if (!profileYaml || saveState.kind === 'saving') return;
    if (localValidationError) {
      setSaveState({ kind: 'error', message: localValidationError });
      return;
    }
    if (editIdx !== null) {
      setSaveState({
        kind: 'error',
        message: 'Finish or cancel the alert you are editing first',
      });
      return;
    }
    setSaveState({ kind: 'saving' });
    const withSched = applyScheduleToYaml(profileYaml, resolved.schedule);
    const newYaml = applyAlertsToYaml(withSched, alerts);
    const secret = process.env.NEXT_PUBLIC_WEB_SHARED_SECRET ?? '';
    try {
      const res = await fetch('/api/onboard/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-web-secret': secret },
        body: JSON.stringify({ yaml: newYaml }),
      });
      const data = (await res.json()) as {
        ok: boolean;
        slug?: string;
        error?: string;
        details?: string[];
      };
      if (!res.ok || !data.ok) {
        setSaveState({
          kind: 'error',
          message: data.error ?? `Save failed (${res.status})`,
          details: data.details,
        });
        return;
      }
      setSaveState({ kind: 'success' });
      router.refresh();
    } catch (err) {
      setSaveState({
        kind: 'error',
        message: err instanceof Error ? err.message : 'save failed',
      });
    }
  }

  if (!profileYaml) return null;

  const disabledSave =
    !dirty ||
    saveState.kind === 'saving' ||
    saveState.kind === 'success' ||
    localValidationError !== null ||
    editIdx !== null;

  const showSubscribeNudge = alerts.length > 0 && pushSubscribed === false;
  const activePreset = SCHEDULE_PRESETS.find((p) => p.id === presetId);
  const showTimeRow = activePreset?.needsTime === true;
  const showDateRow = activePreset?.needsDate === true;

  return (
    <div className="relative" ref={popoverRef}>
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : openEditor())}
        className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-900 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
        aria-expanded={open}
        aria-label="Edit schedule and alerts"
      >
        <CalendarClock className="w-4 h-4" />
        <span className="hidden sm:inline">Schedule &amp; Alerts</span>
        {alerts.length > 0 && (
          <span className="inline-flex items-center justify-center min-w-5 h-5 px-1.5 text-[10px] font-semibold rounded-full bg-blue-600 text-white">
            {alerts.length}
          </span>
        )}
      </button>
      {open && (
        <div className="fixed inset-x-2 top-24 sm:absolute sm:inset-x-auto sm:top-auto sm:left-0 sm:mt-2 z-30 w-auto sm:w-[min(24rem,calc(100vw-2rem))] bg-white dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-xl shadow-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Schedule &amp; Alerts</h3>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
              aria-label="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="max-h-[60vh] overflow-y-auto px-3 py-3 space-y-4">
            <section>
              <h4 className="text-[11px] uppercase tracking-wide font-semibold text-gray-500 dark:text-gray-400 mb-1.5">
                Schedule
              </h4>
              <fieldset>
                <legend className="sr-only">When should this product run?</legend>
                <ul className="space-y-1">
                  {SCHEDULE_PRESETS.map((p) => (
                    <li key={p.id}>
                      <label
                        className={`flex items-center gap-2 rounded-lg px-2 py-1.5 cursor-pointer text-sm ${
                          presetId === p.id
                            ? 'bg-blue-50 dark:bg-blue-900/30 text-gray-900 dark:text-gray-100'
                            : 'hover:bg-gray-50 dark:hover:bg-gray-900/50 text-gray-700 dark:text-gray-300'
                        }`}
                      >
                        <input
                          type="radio"
                          name="schedule-preset"
                          value={p.id}
                          checked={presetId === p.id}
                          onChange={() => setPresetId(p.id)}
                          className="shrink-0"
                        />
                        <span className="flex-1">{p.label}</span>
                        {p.cron && (
                          <code className="text-[10px] text-gray-500 dark:text-gray-400 hidden sm:inline">
                            {p.cron}
                          </code>
                        )}
                      </label>

                      {presetId === p.id && p.id === 'custom' && (
                        <>
                          <input
                            type="text"
                            value={customCron}
                            onChange={(e) => setCustomCron(e.target.value)}
                            placeholder="0 8 * * *  (min hour dom mon dow, UTC)"
                            spellCheck={false}
                            autoComplete="off"
                            className="mt-1 w-full font-mono text-xs px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                          />
                          <div className="mt-1.5 rounded-lg bg-gray-50 dark:bg-gray-900/50 px-2 py-1.5 text-[11px] text-gray-600 dark:text-gray-400 space-y-1">
                            <div>
                              Five fields, all times{' '}
                              <strong>UTC</strong>:{' '}
                              <code className="font-mono">min hour dom mon dow</code>{' '}
                              (use <code className="font-mono">*</code> for
                              &ldquo;any&rdquo;, <code className="font-mono">1-5</code>{' '}
                              for a range, <code className="font-mono">*/6</code> for
                              &ldquo;every 6&rdquo;).
                            </div>
                            <table className="w-full">
                              <tbody>
                                {[
                                  ['0 8 * * *', 'every day at 08:00 UTC'],
                                  ['30 13 * * 1-5', 'weekdays at 13:30 UTC'],
                                  ['0 */6 * * *', 'every 6 hours, on the hour'],
                                  ['15 0 1 * *', '00:15 UTC on the 1st of the month'],
                                ].map(([expr, desc]) => (
                                  <tr key={expr}>
                                    <td className="pr-2 align-top">
                                      <button
                                        type="button"
                                        onClick={() => setCustomCron(expr)}
                                        className="font-mono text-blue-600 dark:text-blue-400 hover:underline"
                                        title="Use this example"
                                      >
                                        {expr}
                                      </button>
                                    </td>
                                    <td className="text-gray-500 dark:text-gray-500">
                                      {desc}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </fieldset>

              {(showTimeRow || showDateRow) && (
                <div className="mt-2 ml-6 grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-2">
                  {showDateRow && (
                    <>
                      <label className="text-[11px] text-gray-600 dark:text-gray-400">
                        Date
                      </label>
                      <input
                        type="date"
                        value={dateStr}
                        onChange={(e) => setDateStr(e.target.value)}
                        className="text-xs px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                    </>
                  )}
                  <label className="text-[11px] text-gray-600 dark:text-gray-400">
                    Time
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      type="time"
                      step={TIME_STEP_SECONDS}
                      value={timeHHMM}
                      onChange={(e) => setTimeHHMM(e.target.value)}
                      className="text-xs px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                    <select
                      value={tz}
                      onChange={(e) => setTz(e.target.value)}
                      className="flex-1 text-xs px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                      aria-label="Time zone"
                    >
                      {tzOptions.map((o) => (
                        <option key={o.id} value={o.id}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              )}

              <div className="text-[11px] text-gray-600 dark:text-gray-400 px-2 mt-2 space-y-0.5">
                <div>{describeSchedule(resolved.schedule)}</div>
                {nextRun && (
                  <div>
                    Next run:{' '}
                    <span className="font-medium">{nextRun.toLocaleString()}</span>{' '}
                    <span className="text-gray-500 dark:text-gray-500">
                      ({nextRun.toISOString().replace('.000', '').replace('T', ' ')})
                    </span>
                  </div>
                )}
                {onceInPast && !localValidationError && (
                  <div className="text-amber-700 dark:text-amber-400">
                    That time is in the past — it will run at the next
                    scheduler tick (within ~15 min).
                  </div>
                )}
              </div>
            </section>

            <section>
              <div className="flex items-center justify-between mb-1.5">
                <h4 className="text-[11px] uppercase tracking-wide font-semibold text-gray-500 dark:text-gray-400">
                  Alerts
                </h4>
                {editIdx === null && (
                  <button
                    type="button"
                    onClick={startAdd}
                    className="inline-flex items-center gap-1 text-[11px] font-medium text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    <Plus className="w-3.5 h-3.5" /> Add alert
                  </button>
                )}
              </div>

              {alerts.length === 0 && editIdx === null && (
                <p className="text-[11px] text-gray-500 dark:text-gray-400 px-2 py-2 rounded-lg bg-gray-50 dark:bg-gray-900/50">
                  No alerts configured. Add one to get a push when the price drops or a vendor surfaces a listing.
                </p>
              )}

              <ul className="space-y-1">
                {alerts.map((rule, i) =>
                  editIdx === i ? (
                    <li key={i}>
                      <AlertForm
                        draft={editDraft}
                        onChange={setEditDraft}
                        onCommit={commitEdit}
                        onCancel={cancelEdit}
                        error={editError}
                      />
                    </li>
                  ) : (
                    <li
                      key={i}
                      className="flex items-center gap-2 px-2 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-900/50 text-xs text-gray-800 dark:text-gray-200"
                    >
                      <Bell className="w-3.5 h-3.5 text-blue-600 dark:text-blue-400 shrink-0" />
                      <span className="flex-1 truncate">{describeRule(rule)}</span>
                      <button
                        type="button"
                        onClick={() => startEdit(i)}
                        className="text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
                        aria-label="Edit alert"
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteRule(i)}
                        className="text-gray-500 hover:text-red-600 dark:text-gray-400 dark:hover:text-red-400"
                        aria-label="Delete alert"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </li>
                  ),
                )}
                {editIdx === -1 && (
                  <li>
                    <AlertForm
                      draft={editDraft}
                      onChange={setEditDraft}
                      onCommit={commitEdit}
                      onCancel={cancelEdit}
                      error={editError}
                    />
                  </li>
                )}
              </ul>

              {showSubscribeNudge && (
                <p className="text-[11px] text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-900/40 rounded-lg px-2 py-1.5 mt-2">
                  Tap <strong>Enable Alerts</strong> in the toolbar above to receive push notifications on this device.
                </p>
              )}
            </section>
          </div>

          <div className="px-3 py-2 border-t border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/50 space-y-2">
            {saveState.kind === 'success' && (
              <div className="flex items-center gap-2 text-[11px] text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-900/40 rounded-lg px-2 py-1.5">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>Saved. The scheduler will pick this up on its next tick.</span>
              </div>
            )}
            {saveState.kind === 'error' && (
              <div className="text-[11px] text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-900/40 rounded-lg px-2 py-1.5 space-y-1">
                <div className="font-medium">{saveState.message}</div>
                {saveState.details && saveState.details.length > 0 && (
                  <ul className="list-disc ml-4 space-y-0.5">
                    {saveState.details.slice(0, 4).map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {localValidationError && saveState.kind === 'idle' && (
              <div className="text-[11px] text-red-700 dark:text-red-400 px-2">
                {localValidationError}
              </div>
            )}
            <button
              type="button"
              onClick={onSave}
              disabled={disabledSave}
              className="w-full flex items-center justify-center gap-2 rounded-lg bg-blue-600 text-white text-sm font-medium px-4 py-2 disabled:bg-gray-300 disabled:dark:bg-gray-700 disabled:cursor-not-allowed hover:bg-blue-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {saveState.kind === 'saving' ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : null}
              {saveState.kind === 'saving' ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function AlertForm({
  draft,
  onChange,
  onCommit,
  onCancel,
  error,
}: {
  draft: AlertDraft;
  onChange: (next: AlertDraft) => void;
  onCommit: () => void;
  onCancel: () => void;
  error: string | null;
}) {
  return (
    <div className="rounded-lg border border-blue-200 dark:border-blue-900/40 bg-blue-50/40 dark:bg-blue-900/10 p-2 space-y-2">
      <div className="flex items-center gap-2">
        <label className="text-[11px] text-gray-700 dark:text-gray-300 shrink-0">Kind</label>
        <select
          value={draft.kind}
          onChange={(e) => {
            const kind = e.target.value as AlertDraft['kind'];
            if (kind === 'price_below') {
              onChange({ kind: 'price_below', threshold_usd: '', condition: '' });
            } else {
              onChange({ kind: 'vendor_seen', host: '' });
            }
          }}
          className="flex-1 text-xs px-2 py-1 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        >
          <option value="price_below">Price drops below threshold</option>
          <option value="vendor_seen">Vendor surfaces a listing</option>
        </select>
      </div>

      {draft.kind === 'price_below' ? (
        <>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-gray-700 dark:text-gray-300 shrink-0 w-20">Threshold $</label>
            <input
              type="number"
              inputMode="decimal"
              step="0.01"
              min="0"
              value={draft.threshold_usd}
              onChange={(e) =>
                onChange({ ...draft, threshold_usd: e.target.value })
              }
              placeholder="99.99"
              className="flex-1 text-xs px-2 py-1 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-gray-700 dark:text-gray-300 shrink-0 w-20">Condition</label>
            <select
              value={draft.condition}
              onChange={(e) =>
                onChange({
                  ...draft,
                  condition: e.target.value as AlertDraft extends { kind: 'price_below' }
                    ? AlertDraft['condition']
                    : never,
                })
              }
              className="flex-1 text-xs px-2 py-1 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
            >
              <option value="">Any condition</option>
              <option value="new">New only</option>
              <option value="used">Used only</option>
              <option value="refurbished">Refurbished only</option>
            </select>
          </div>
        </>
      ) : (
        <div className="flex items-center gap-2">
          <label className="text-[11px] text-gray-700 dark:text-gray-300 shrink-0 w-20">Host</label>
          <input
            type="text"
            value={draft.host}
            onChange={(e) => onChange({ ...draft, host: e.target.value })}
            placeholder="amazon.com"
            spellCheck={false}
            autoComplete="off"
            className="flex-1 text-xs px-2 py-1 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
          />
        </div>
      )}

      {error && (
        <div className="text-[11px] text-red-700 dark:text-red-400">{error}</div>
      )}

      <div className="flex items-center justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onCancel}
          className="text-[11px] text-gray-600 dark:text-gray-400 hover:underline"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onCommit}
          className="text-[11px] font-medium px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700"
        >
          Done
        </button>
      </div>
    </div>
  );
}

export default ScheduleEditorButton;
