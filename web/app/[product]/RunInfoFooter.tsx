'use client';

import { useEffect, useState } from 'react';

function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem.toString().padStart(2, '0')}s`;
}

interface FooterInfo {
  // Authoritative instant of the most recent run of ANY kind (scheduled or
  // on-demand), derived from the newest data CSV. This is what makes the time
  // correct even when the last run was scheduled (the Actions API the duration
  // comes from only ever sees on-demand runs).
  completedAt: string;
  // Present only when the latest run is the same one the on-demand Actions API
  // reports (a scheduled run has no per-product duration we can attribute).
  durationMs: number | null;
  conclusion: string | null;
}

export function RunInfoFooter({ lastRun }: { lastRun: FooterInfo }) {
  // The completed timestamp must format in the user's local timezone, but
  // this component is rendered both during SSR (Vercel = UTC) and after
  // hydration in the browser. Format only after mount so the SSR markup
  // matches the initial client render (a placeholder), and the localized
  // string drops in on the next paint. This avoids a hydration mismatch
  // warning while still giving the user the correct local time.
  const [completedLabel, setCompletedLabel] = useState<string | null>(null);

  useEffect(() => {
    // Client-only formatting is the whole point — the placeholder above
    // matches what SSR rendered, so this single post-mount setState is
    // both intentional and bounded (one re-render, no cascade).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setCompletedLabel(
      new Date(lastRun.completedAt).toLocaleString(undefined, {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    );
  }, [lastRun.completedAt]);

  const duration =
    lastRun.durationMs !== null ? formatDuration(lastRun.durationMs) : null;
  const failed = lastRun.conclusion && lastRun.conclusion !== 'success';

  return (
    <div
      className={`mt-6 pt-4 border-t border-gray-200 dark:border-gray-800 text-xs ${
        failed ? 'text-red-600 dark:text-red-400' : 'text-gray-500 dark:text-gray-400'
      }`}
    >
      Last run completed{' '}
      <time dateTime={lastRun.completedAt}>
        {completedLabel ?? '…'}
      </time>
      {duration ? ` · took ${duration}` : ''}
      {failed ? ` · conclusion: ${lastRun.conclusion}` : ''}
    </div>
  );
}

export default RunInfoFooter;
