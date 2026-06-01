'use client';

import { useEffect, useState } from 'react';

interface CardRunStatusProps {
  // Exact run instant from the newest data CSV (covers scheduled + on-demand).
  lastRunIso: string | null;
  // Latest report date (YYYY-MM-DD) — shown date-only when no CSV exists yet.
  fallbackDate: string | null;
  // 'running'  — a run for this product is actively in flight.
  // 'waiting'  — on a schedule but idle right now (armed, not running).
  // 'idle'     — no schedule and not running.
  status: 'running' | 'waiting' | 'idle';
  // When running: the instant the active run began (run_started_at). Shown as
  // "since <time>" instead of the stale last-run timestamp. Null if unknown.
  runningSinceIso: string | null;
}

export function CardRunStatus({
  lastRunIso,
  fallbackDate,
  status,
  runningSinceIso,
}: CardRunStatusProps) {
  // While running, the relevant instant is when THIS run began; otherwise it's
  // the last completed run. toLocaleString resolves differently on the server
  // (Vercel = UTC) and in the browser (user's zone), so render a
  // timezone-independent placeholder for SSR + first client paint (an ISO date
  // slice — identical on both), then drop in the localized date+time after
  // mount. Same approach as RunInfoFooter; avoids a hydration mismatch.
  const primaryIso = status === 'running' ? runningSinceIso : lastRunIso;
  const [localLabel, setLocalLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!primaryIso) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLocalLabel(
      new Date(primaryIso).toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      }),
    );
  }, [primaryIso]);

  // When running we only ever show the run's own start time — never the stale
  // last-run timestamp (that was the bug). When waiting/idle, fall back to the
  // last report date if no CSV instant exists.
  const timeText = primaryIso
    ? (localLabel ?? primaryIso.slice(0, 10))
    : status === 'running'
      ? null
      : fallbackDate;

  if (status === 'idle' && !timeText) return null;

  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium text-gray-500 bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded-full whitespace-nowrap pointer-events-none">
      {status === 'running' && (
        <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-500 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
          </span>
          Running
        </span>
      )}
      {status === 'waiting' && (
        <span className="inline-flex items-center gap-1 text-amber-600 dark:text-amber-400">
          <span className="inline-flex h-2 w-2 rounded-full bg-amber-500" />
          Waiting to run
        </span>
      )}
      {timeText && (
        <time
          dateTime={primaryIso ?? undefined}
          className={status === 'waiting' ? 'font-normal text-gray-400 dark:text-gray-500' : ''}
        >
          {status === 'running' && primaryIso
            ? `since ${timeText}`
            : status === 'waiting'
              ? `(last run: ${timeText})`
              : timeText}
        </time>
      )}
    </span>
  );
}

export default CardRunStatus;
