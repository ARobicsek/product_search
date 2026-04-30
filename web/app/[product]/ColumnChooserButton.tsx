'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  Columns3,
  Loader2,
  X,
} from 'lucide-react';
import {
  DEFAULT_REPORT_COLUMNS,
  REPORT_COLUMN_DEFS,
  applyReportColumnsToYaml,
  readReportColumnsFromYaml,
} from '@/lib/report-columns';

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'success' }
  | { kind: 'error'; message: string; details?: string[] };

export function ColumnChooserButton({
  profileYaml,
}: {
  product: string;
  profileYaml: string | null;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle' });
  const initial = profileYaml ? readReportColumnsFromYaml(profileYaml) : null;
  const [columns, setColumns] = useState<string[]>(
    initial && initial.length > 0 ? initial : [...DEFAULT_REPORT_COLUMNS]
  );
  const popoverRef = useRef<HTMLDivElement | null>(null);

  // Close on outside click — mobile users tap outside the panel to dismiss.
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

  function openChooser() {
    // Reset to the current-profile state every time the panel opens, so a
    // failed save followed by a re-open shows the canonical-on-disk
    // selection rather than the user's mid-edit local list.
    const fresh = profileYaml ? readReportColumnsFromYaml(profileYaml) : null;
    setColumns(fresh && fresh.length > 0 ? fresh : [...DEFAULT_REPORT_COLUMNS]);
    setSaveState({ kind: 'idle' });
    setOpen(true);
  }

  const selectedSet = new Set(columns);
  const availableColumns = REPORT_COLUMN_DEFS.filter((c) => !selectedSet.has(c.id));

  function toggle(id: string) {
    if (selectedSet.has(id)) {
      // Don't let the user remove the very last column — at least one
      // must remain or the table renders empty.
      if (columns.length <= 1) return;
      setColumns(columns.filter((c) => c !== id));
    } else {
      setColumns([...columns, id]);
    }
  }

  function move(id: string, delta: -1 | 1) {
    const idx = columns.indexOf(id);
    if (idx < 0) return;
    const next = idx + delta;
    if (next < 0 || next >= columns.length) return;
    const copy = [...columns];
    [copy[idx], copy[next]] = [copy[next], copy[idx]];
    setColumns(copy);
  }

  async function onSave() {
    if (!profileYaml || saveState.kind === 'saving') return;
    setSaveState({ kind: 'saving' });

    const newYaml = applyReportColumnsToYaml(profileYaml, columns);
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
      // Refresh the page so the toolbar's profileYaml prop picks up the
      // committed change — the column set on the *next* run will then
      // pre-fill correctly when reopening this chooser.
      router.refresh();
    } catch (err) {
      setSaveState({
        kind: 'error',
        message: err instanceof Error ? err.message : 'save failed',
      });
    }
  }

  const dirty = JSON.stringify(columns) !== JSON.stringify(initial ?? DEFAULT_REPORT_COLUMNS);
  const disabledSave =
    !profileYaml ||
    !dirty ||
    saveState.kind === 'saving' ||
    saveState.kind === 'success' ||
    columns.length === 0;

  if (!profileYaml) {
    // Without a profile we can't reorder — hide the button rather than
    // present a non-functional control.
    return null;
  }

  return (
    <div className="relative" ref={popoverRef}>
      <button
        type="button"
        onClick={() => (open ? setOpen(false) : openChooser())}
        className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-900 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
        aria-expanded={open}
        aria-label="Edit report columns"
      >
        <Columns3 className="w-4 h-4" />
        <span className="hidden sm:inline">Columns</span>
      </button>
      {open && (
        <div className="absolute right-0 mt-2 z-30 w-[min(22rem,calc(100vw-2rem))] bg-white dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-xl shadow-lg overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
            <h3 className="text-sm font-semibold">Report columns</h3>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
              aria-label="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          <div className="max-h-[60vh] overflow-y-auto">
            <div className="px-3 py-2">
              <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">
                Selected ({columns.length})
              </div>
              <ul className="space-y-1">
                {columns.map((id, idx) => {
                  const def = REPORT_COLUMN_DEFS.find((c) => c.id === id);
                  if (!def) return null;
                  return (
                    <li
                      key={id}
                      className="flex items-center gap-2 rounded-lg bg-blue-50 dark:bg-blue-900/30 px-2 py-1.5"
                    >
                      <input
                        type="checkbox"
                        checked
                        onChange={() => toggle(id)}
                        className="shrink-0"
                        aria-label={`Remove ${def.label}`}
                      />
                      <span className="flex-1 text-sm text-gray-900 dark:text-gray-100 truncate">
                        {def.label}
                      </span>
                      <button
                        type="button"
                        onClick={() => move(id, -1)}
                        disabled={idx === 0}
                        className="p-1 rounded hover:bg-blue-100 dark:hover:bg-blue-900/60 disabled:opacity-30 disabled:cursor-not-allowed"
                        aria-label={`Move ${def.label} up`}
                      >
                        <ArrowUp className="w-3.5 h-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => move(id, 1)}
                        disabled={idx === columns.length - 1}
                        className="p-1 rounded hover:bg-blue-100 dark:hover:bg-blue-900/60 disabled:opacity-30 disabled:cursor-not-allowed"
                        aria-label={`Move ${def.label} down`}
                      >
                        <ArrowDown className="w-3.5 h-3.5" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>

            {availableColumns.length > 0 && (
              <div className="px-3 py-2 border-t border-gray-100 dark:border-gray-800/60">
                <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">
                  Available
                </div>
                <ul className="space-y-1">
                  {availableColumns.map((def) => (
                    <li
                      key={def.id}
                      className="flex items-center gap-2 rounded-lg px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-900/50"
                    >
                      <input
                        type="checkbox"
                        checked={false}
                        onChange={() => toggle(def.id)}
                        className="shrink-0"
                        aria-label={`Add ${def.label}`}
                      />
                      <span className="flex-1 text-sm text-gray-700 dark:text-gray-300 truncate">
                        {def.label}
                      </span>
                      <span className="text-[10px] text-gray-400 dark:text-gray-500 truncate hidden sm:inline">
                        {def.description}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div className="px-3 py-2 border-t border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900/50 space-y-2">
            {saveState.kind === 'success' && (
              <div className="flex items-center gap-2 text-[11px] text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-900/40 rounded-lg px-2 py-1.5">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>Saved. Will apply on the next run.</span>
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
            <button
              type="button"
              onClick={onSave}
              disabled={disabledSave}
              className="w-full flex items-center justify-center gap-2 rounded-lg bg-blue-600 text-white text-sm font-medium px-4 py-2 disabled:bg-gray-300 disabled:dark:bg-gray-700 disabled:cursor-not-allowed hover:bg-blue-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {saveState.kind === 'saving' ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : null}
              {saveState.kind === 'saving' ? 'Saving…' : 'Save column changes'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default ColumnChooserButton;
