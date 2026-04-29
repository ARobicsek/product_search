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

const POLL_INTERVAL_MS = 5_000;
const POLL_TIMEOUT_MS = 15 * 60_000;

export function RunNowButton({ product }: { product: string }) {
  const [state, setState] = useState<RunState>('idle');
  const [message, setMessage] = useState<string>('');
  const [conclusion, setConclusion] = useState<string | null>(null);
  const [isRefreshing, startRefresh] = useTransition();
  const router = useRouter();
  const cancelled = useRef(false);

  useEffect(() => {
    return () => {
      cancelled.current = true;
    };
  }, []);

  // Once router.refresh() finishes streaming the new RSC payload, clear the
  // toolbar back to idle so the "Done. Loading new report…" message goes away.
  useEffect(() => {
    if (state !== 'done' || isRefreshing) return;
    const id = setTimeout(() => {
      setState('idle');
      setMessage('');
      setConclusion(null);
    }, 0);
    return () => clearTimeout(id);
  }, [state, isRefreshing]);

  async function pollUntilComplete(since: string) {
    const deadline = Date.now() + POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (cancelled.current) return;
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
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
          setConclusion(data.conclusion ?? null);
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
    setState('dispatching');
    setMessage('Triggering run…');
    setConclusion(null);

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
          className={`text-[11px] max-w-[12rem] text-right truncate ${
            state === 'error'
              ? 'text-red-600 dark:text-red-400'
              : 'text-gray-500 dark:text-gray-400'
          }`}
          title={message}
        >
          {conclusion && state !== 'done' ? `${message}` : message}
        </span>
      )}
    </div>
  );
}
