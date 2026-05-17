'use client';

import { useEffect, useState } from 'react';

interface CardRunStatusProps {
  // Exact run instant from the newest data CSV (covers scheduled + on-demand).
  lastRunIso: string | null;
  // Latest report date (YYYY-MM-DD) — shown date-only when no CSV exists yet.
  fallbackDate: string | null;
  running: boolean;
}

export function CardRunStatus({ lastRunIso, fallbackDate, running }: CardRunStatusProps) {
  // toLocaleString resolves differently on the server (Vercel = UTC) and in the
  // browser (user's zone). Render a timezone-independent placeholder for SSR +
  // first client paint (a plain string slice of the ISO date — identical on
  // both), then drop in the localized date+time after mount. Same approach as
  // RunInfoFooter; avoids a hydration mismatch.
  const [localLabel, setLocalLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!lastRunIso) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLocalLabel(
      new Date(lastRunIso).toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      }),
    );
  }, [lastRunIso]);

  const lastRunText = lastRunIso
    ? (localLabel ?? lastRunIso.slice(0, 10))
    : fallbackDate;

  if (!running && !lastRunText) return null;

  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium text-gray-500 bg-gray-100 dark:bg-gray-800 px-2 py-1 rounded-full whitespace-nowrap pointer-events-none">
      {running && (
        <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-500 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-green-500" />
          </span>
          Running
        </span>
      )}
      {lastRunText && (
        <time dateTime={lastRunIso ?? undefined}>{lastRunText}</time>
      )}
    </span>
  );
}

export default CardRunStatus;
