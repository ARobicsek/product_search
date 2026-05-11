'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { CalendarClock, CheckCircle2, Loader2, X } from 'lucide-react';
import {
  applyScheduleToYaml,
  detectPreset,
  nextCronTick,
  readScheduleFromYaml,
  SCHEDULE_PRESETS,
  validateCron,
  type ScheduleConfig,
} from '@/lib/schedule';

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'success' }
  | { kind: 'error'; message: string; details?: string[] };

export function ScheduleEditorButton({
  profileYaml,
}: {
  product: string;
  profileYaml: string | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle' });

  const initialSchedule = profileYaml ? readScheduleFromYaml(profileYaml) : null;
  const initialPresetId = detectPreset(initialSchedule);

  const [presetId, setPresetId] = useState<string>(initialPresetId);
  const [customCron, setCustomCron] = useState<string>(
    initialPresetId === 'custom' ? initialSchedule?.cron ?? '' : ''
  );
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

  function openEditor() {
    const fresh = profileYaml ? readScheduleFromYaml(profileYaml) : null;
    const freshPreset = detectPreset(fresh);
    setPresetId(freshPreset);
    setCustomCron(freshPreset === 'custom' ? fresh?.cron ?? '' : '');
    setSaveState({ kind: 'idle' });
    setOpen(true);
  }

  function resolveSchedule(): { schedule: ScheduleConfig | null; error: string | null } {
    if (presetId === 'none') return { schedule: null, error: null };
    if (presetId === 'custom') {
      const err = validateCron(customCron);
      if (err) return { schedule: null, error: err };
      return { schedule: { cron: customCron.trim() }, error: null };
    }
    const preset = SCHEDULE_PRESETS.find((p) => p.id === presetId);
    if (!preset || !preset.cron) return { schedule: null, error: 'invalid preset' };
    return { schedule: { cron: preset.cron }, error: null };
  }

  const resolved = resolveSchedule();
  const localValidationError = resolved.error;
  const nextRun =
    resolved.schedule && !localValidationError
      ? nextCronTick(resolved.schedule.cron)
      : null;

  const initialPayload = JSON.stringify(initialSchedule);
  const currentPayload = JSON.stringify(resolved.schedule);
  const dirty = initialPayload !== currentPayload;

  async function onSave() {
    if (!profileYaml || saveState.kind === 'saving') return;
    if (localValidationError) {
      setSaveState({ kind: 'error', message: localValidationError });
      return;
    }
    setSaveState({ kind: 'saving' });
    const newYaml = applyScheduleToYaml(profileYaml, resolved.schedule);
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
    localValidationError !== null;

  return (
    <div className="relative" ref={popoverRef}>
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : openEditor())}
        className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-900 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
        aria-expanded={open}
        aria-label="Edit schedule"
      >
        <CalendarClock className="w-4 h-4" />
        <span className="hidden sm:inline">Schedule</span>
      </button>
      {open && (
        <div className="absolute right-0 mt-2 z-30 w-[min(22rem,calc(100vw-2rem))] bg-white dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-xl shadow-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Schedule</h3>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
              aria-label="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="max-h-[60vh] overflow-y-auto px-3 py-2 space-y-2">
            <fieldset>
              <legend className="sr-only">Schedule preset</legend>
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
                  </li>
                ))}
                <li>
                  <label
                    className={`flex items-center gap-2 rounded-lg px-2 py-1.5 cursor-pointer text-sm ${
                      presetId === 'custom'
                        ? 'bg-blue-50 dark:bg-blue-900/30 text-gray-900 dark:text-gray-100'
                        : 'hover:bg-gray-50 dark:hover:bg-gray-900/50 text-gray-700 dark:text-gray-300'
                    }`}
                  >
                    <input
                      type="radio"
                      name="schedule-preset"
                      value="custom"
                      checked={presetId === 'custom'}
                      onChange={() => setPresetId('custom')}
                      className="shrink-0"
                    />
                    <span className="flex-1">Custom cron</span>
                  </label>
                  {presetId === 'custom' && (
                    <input
                      type="text"
                      value={customCron}
                      onChange={(e) => setCustomCron(e.target.value)}
                      placeholder="0 8 * * *"
                      spellCheck={false}
                      autoComplete="off"
                      className="mt-1 w-full font-mono text-xs px-2 py-1.5 border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500"
                    />
                  )}
                </li>
              </ul>
            </fieldset>

            {nextRun && (
              <div className="text-[11px] text-gray-600 dark:text-gray-400 px-2">
                Next run: <span className="font-medium">{nextRun.toLocaleString()}</span>
                {' '}
                <span className="text-gray-500 dark:text-gray-500">
                  ({nextRun.toISOString().replace('.000', '').replace('T', ' ')})
                </span>
              </div>
            )}
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
              {saveState.kind === 'saving' ? 'Saving…' : 'Save schedule'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default ScheduleEditorButton;
