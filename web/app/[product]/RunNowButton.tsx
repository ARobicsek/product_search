'use client';

import { useEffect, useRef, useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, Play, RefreshCw } from 'lucide-react';

type RunState = 'idle' | 'dispatching' | 'polling' | 'done' | 'error';

interface RunStatusResponse {
  ok: boolean;
  state?: 'pending' | 'queued' | 'in_progress' | 'completed';
  conclusion?: string | null;
  htmlUrl?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  error?: string;
}

const POLL_INTERVAL_FAST_MS = 2_000;
const POLL_INTERVAL_SLOW_MS = 5_000;
const POLL_FAST_WINDOW_MS = 30_000;
const POLL_TIMEOUT_MS = 15 * 60_000;

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem.toString().padStart(2, '0')}s`;
}

function formatRelativeAgo(iso: string): string {
  const diffMs = Date.now() - Date.parse(iso);
  if (Number.isNaN(diffMs) || diffMs < 0) return '';
  const s = Math.floor(diffMs / 1000);
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export interface LastRun {
  completedAt: string;
  durationMs: number;
  conclusion: string | null;
}

export function RunNowButton({
  product,
  lastRun,
}: {
  product: string;
  lastRun?: LastRun | null;
}) {
  const [state, setState] = useState<RunState>('idle');
  const [message, setMessage] = useState<string>('');
  const [elapsed, setElapsed] = useState<string>('');
  const [isRefreshing, startRefresh] = useTransition();
  const router = useRouter();
  const cancelled = useRef(false);
  const startedAtRef = useRef<number>(0);

  useEffect(() => {
    return () => {
      cancelled.current = true;
    };
  }, []);

  useEffect(() => {
    if (state !== 'dispatching' && state !== 'polling') return;
    const tick = () => setElapsed(formatElapsed(Date.now() - startedAtRef.current));
    tick();
    const id = setInterval(tick, 1000);
    return () => {
      clearInterval(id);
      setElapsed('');
    };
  }, [state]);

  // Once router.refresh() finishes streaming the new RSC payload, clear the
  // toolbar back to idle so the "Done. Loading new report…" message goes away.
  useEffect(() => {
    if (state !== 'done' || isRefreshing) return;
    const id = setTimeout(() => {
      setState('idle');
      setMessage('');
    }, 0);
    return () => clearTimeout(id);
  }, [state, isRefreshing]);

  async function pollUntilComplete(since: string) {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    const pollStart = Date.now();
    while (Date.now() < deadline) {
      if (cancelled.current) return;
      const interval =
        Date.now() - pollStart < POLL_FAST_WINDOW_MS
          ? POLL_INTERVAL_FAST_MS
          : POLL_INTERVAL_SLOW_MS;
      await new Promise((r) => setTimeout(r, interval));
      if (cancelled.current) return;

      try {
        const res = await fetch(
          `/api/run-status?product=${encodeURIComponent(product)}&since=${encodeURIComponent(since)}`,
          { cache: 'no-store' },
        );
        const data = (await res.json()) as RunStatusResponse;
        if (!data.ok) {
          setMessage(data.error ?? 'Run-status query failed');
          continue;
        }
        if (data.state === 'completed') {
          if (data.conclusion === 'success') {
            setMessage('Refreshing report…');
            await fetch('/api/revalidate', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ product }),
            }).catch(() => {});
            setState('done');
            setMessage('Done. Loading new report…');
            startRefresh(() => {
              router.refresh();
            });
          } else {
            setState('error');
            setMessage(`Run finished with conclusion: ${data.conclusion ?? 'unknown'}`);
          }
          return;
        }
        if (data.state === 'queued') {
          setMessage('Queued on GitHub Actions…');
        } else if (data.state === 'in_progress') {
          setMessage('Running…');
        } else {
          setMessage('Waiting for run to start…');
        }
      } catch (err) {
        setMessage(err instanceof Error ? err.message : 'Polling error');
      }
    }
    setState('error');
    setMessage('Timed out waiting for run to complete');
  }

  async function onClick() {
    if (state === 'dispatching' || state === 'polling') return;
    startedAtRef.current = Date.now();
    setState('dispatching');
    setMessage('Triggering run…');

    const secret = process.env.NEXT_PUBLIC_WEB_SHARED_SECRET ?? '';
    try {
      const res = await fetch('/api/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-web-secret': secret },
        body: JSON.stringify({ product }),
      });
      const data = (await res.json()) as { ok: boolean; dispatchedAt?: string; error?: string };
      if (!res.ok || !data.ok || !data.dispatchedAt) {
        setState('error');
        setMessage(data.error ?? `Dispatch failed (${res.status})`);
        return;
      }
      setState('polling');
      setMessage('Dispatched. Waiting for run to register…');
      pollUntilComplete(data.dispatchedAt);
    } catch (err) {
      setState('error');
      setMessage(err instanceof Error ? err.message : 'Dispatch failed');
    }
  }

  const inFlight = state === 'dispatching' || state === 'polling';

  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={onClick}
        disabled={inFlight}
        className={`flex items-center text-sm font-medium px-3 py-1.5 rounded-full transition focus:outline-none focus:ring-2 focus:ring-blue-500 ${
          inFlight
            ? 'bg-gray-200 dark:bg-gray-800 text-gray-500 cursor-not-allowed'
            : state === 'error'
            ? 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-400 hover:bg-red-100'
            : 'bg-blue-600 text-white hover:bg-blue-700'
        }`}
      >
        {inFlight ? (
          <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />
        ) : state === 'error' ? (
          <RefreshCw className="w-4 h-4 mr-1.5" />
        ) : (
          <Play className="w-4 h-4 mr-1.5" />
        )}
        {inFlight ? 'Running' : state === 'error' ? 'Retry' : 'Run now'}
      </button>
      {message && (
        <span
          className={`text-[11px] max-w-56 text-right truncate ${
            state === 'error'
              ? 'text-red-600 dark:text-red-400'
              : 'text-gray-500 dark:text-gray-400'
          }`}
          title={message}
        >
          {elapsed ? `${message} (${elapsed})` : message}
        </span>
      )}
      {!message && lastRun && (
        <span
          className="text-[11px] max-w-56 text-right truncate text-gray-500 dark:text-gray-400"
          title={`Completed ${new Date(lastRun.completedAt).toLocaleString()}${
            lastRun.conclusion && lastRun.conclusion !== 'success'
              ? ` (${lastRun.conclusion})`
              : ''
          }`}
        >
          Last run: {formatElapsed(lastRun.durationMs)} ·{' '}
          {formatRelativeAgo(lastRun.completedAt) || 'completed'}
        </span>
      )}
    </div>
  );
}
