'use client';

import { Loader2 } from 'lucide-react';
import { useRunRunning } from './runState';

export function ReportSection({ children }: { children: React.ReactNode }) {
  const running = useRunRunning();

  if (running) {
    return (
      <div className="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 p-8 text-center space-y-3 mt-2">
        <Loader2 className="w-6 h-6 mx-auto animate-spin text-blue-600 dark:text-blue-400" />
        <p className="text-sm text-gray-600 dark:text-gray-400">
          Running a fresh search. The previous report is hidden so you don&apos;t
          act on stale numbers — it&apos;ll be replaced when the run finishes.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
