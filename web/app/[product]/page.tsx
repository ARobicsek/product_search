import Link from 'next/link';
import {
  getLastRunInstant,
  getProductProfileContent,
  getProductProfileExists,
  getProductReports,
  getReportContent,
  getReportJsonSidecar,
} from '@/lib/github';
import { getActiveRuns, getLastCompletedRun } from '@/lib/dispatch';
import { readScheduleFromYaml } from '@/lib/schedule';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronLeft, History, Sparkles } from 'lucide-react';
import { notFound } from 'next/navigation';
import { RunNowButton } from './RunNowButton';
import { ColumnChooserButton } from './ColumnChooserButton';
import { ScheduleEditorButton } from './ScheduleEditorButton';
import { RunInfoFooter } from './RunInfoFooter';
import { ReportSection } from './ReportSection';
import { ResultView } from './ResultView';
import { parseSidecar } from './result-types';

// Force every request through SSR so the Vercel edge can never serve a
// rendered HTML/RSC payload from before a Run-now's commit. The `cache: 'no-store'`
// fetches inside this page guarantee fresh data; `force-dynamic` guarantees fresh
// rendering on top of that.
export const dynamic = 'force-dynamic';

// Top-level `display_name:` from a profile.yaml — the product's real name,
// with capitals only where they belong (e.g. "AMD EPYC 9255"). Mirrors the
// home page so the title is consistent across both surfaces. Regex follows
// the lightweight reader in lib/schedule.ts rather than a full YAML parse.
function displayNameFromProfile(yamlText: string | null): string | null {
  if (!yamlText) return null;
  const m = yamlText.match(/^display_name:[ \t]*(.+?)[ \t]*\r?$/m);
  if (!m) return null;
  return m[1].replace(/^["']|["']$/g, '').trim() || null;
}

function prettifySlug(slug: string): string {
  return slug.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export default async function ProductPage({
  params,
  searchParams,
}: {
  params: Promise<{ product: string }>;
  searchParams: Promise<{ date?: string }>;
}) {
  const { product } = await params;
  const { date } = await searchParams;

  const [reports, lastRun, lastRunIso, profileYaml, activeRuns] = await Promise.all([
    getProductReports(product),
    getLastCompletedRun(product).catch(() => null),
    // Authoritative most-recent-run instant (scheduled OR on-demand). The
    // Actions-API `lastRun` above only ever sees on-demand runs, so after a
    // scheduled run its timestamp is stale — this CSV-derived instant fixes it.
    getLastRunInstant(product).catch(() => null),
    getProductProfileContent(product).catch(() => null),
    getActiveRuns().catch(() => ({
      onDemand: [] as { title: string; startedIso: string | null }[],
      scheduledTickActive: false,
      scheduledTickStartedIso: null as string | null,
    })),
  ]);

  // Detect a run already in flight (started from the home page, another
  // device, or the scheduler) so the detail page reflects it on load — not
  // just runs the user kicks off in this tab. Same attribution logic as the
  // home page: an on-demand run carries the slug in its title; a scheduler
  // tick has no per-product title, so it's attributed to any product with a
  // schedule block. `RunNowButton` initializes into its running state from this.
  const hasSchedule = !!profileYaml && readScheduleFromYaml(profileYaml) !== null;
  const onDemandMatch = activeRuns.onDemand.find((r) => r.title.includes(product));
  let initialRun: { sinceIso: string | null; kind: 'ondemand' | 'scheduled' } | null = null;
  if (onDemandMatch) {
    initialRun = { sinceIso: onDemandMatch.startedIso, kind: 'ondemand' };
  } else if (activeRuns.scheduledTickActive && hasSchedule) {
    initialRun = { sinceIso: activeRuns.scheduledTickStartedIso, kind: 'scheduled' };
  }

  const displayName = displayNameFromProfile(profileYaml) || prettifySlug(product);

  // Footer time = the true latest run. Keep the on-demand duration/conclusion
  // only when that run IS the latest one (instants within 10 min — the worker
  // writes the CSV a minute or two before the workflow marks itself complete).
  // A much-newer CSV instant means the latest run was scheduled: show the time
  // only (no per-product duration exists for a multi-product scheduler tick).
  let footerInfo:
    | { completedAt: string; durationMs: number | null; conclusion: string | null }
    | null = null;
  if (lastRunIso || lastRun) {
    const sameRun =
      lastRun && lastRunIso
        ? Math.abs(Date.parse(lastRunIso) - Date.parse(lastRun.completedAt)) <=
          10 * 60_000
        : !lastRunIso;
    footerInfo = {
      completedAt: lastRunIso ?? lastRun!.completedAt,
      durationMs: sameRun && lastRun ? lastRun.durationMs : null,
      conclusion: sameRun && lastRun ? lastRun.conclusion : null,
    };
  }

  if (reports.length === 0) {
    // Fresh onboard: profile exists but no report has been generated yet.
    // Render an empty state with a Run Now button instead of 404'ing.
    const onboarded = await getProductProfileExists(product);
    if (!onboarded) return notFound();
    return (
      <main className="min-h-screen bg-gray-50 dark:bg-[#0a0a0a] pb-12">
        <header className="sticky top-0 z-10 bg-white/80 dark:bg-[#0a0a0a]/80 backdrop-blur-md border-b border-gray-200 dark:border-gray-800 p-4 flex items-center justify-between">
          <Link
            href="/"
            className="flex items-center text-blue-600 dark:text-blue-400 hover:text-blue-800 text-sm font-medium"
          >
            <ChevronLeft className="w-5 h-5 mr-1" />
            Back
          </Link>
          <h1 className="font-semibold truncate max-w-[50%]">
            {displayName}
          </h1>
          <span className="w-12" aria-hidden />
        </header>

        <div className="px-4 pt-4 max-w-2xl mx-auto w-full flex items-center justify-end gap-3">
          <ScheduleEditorButton product={product} profileYaml={profileYaml} />
          <ColumnChooserButton product={product} profileYaml={profileYaml} />
          <Link
            href={`/onboard?edit=${product}`}
            className="inline-flex items-center justify-center px-4 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-900 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition"
          >
            Edit Profile
          </Link>
          <RunNowButton product={product} lastRun={lastRun} initialRun={initialRun} />
        </div>

        <section className="p-6 max-w-2xl mx-auto w-full mt-2">
          <div className="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 p-8 text-center space-y-3">
            <div className="mx-auto w-10 h-10 rounded-full bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center">
              <Sparkles className="w-5 h-5 text-blue-600 dark:text-blue-400" />
            </div>
            <h2 className="text-lg font-semibold">Profile saved</h2>
            <p className="text-sm text-gray-600 dark:text-gray-400">
              No report yet for <span className="font-mono">{product}</span>.
              The first scheduled run will generate one, or click <strong>Run now</strong> above
              to trigger an on-demand run immediately.
            </p>
          </div>
        </section>
      </main>
    );
  }

  // Use requested date or the latest one
  const selectedDate = date && reports.includes(date) ? date : reports[0];

  // ADR-096: fetch the structured JSON sidecar in parallel with the
  // markdown. New reports (post-ADR-096) have both — the React UI
  // prefers the sidecar. Older reports have only the markdown and fall
  // through to the legacy renderer.
  const [content, sidecarRaw] = await Promise.all([
    getReportContent(product, selectedDate),
    getReportJsonSidecar(product, selectedDate),
  ]);

  if (!content) {
    return notFound();
  }

  const sidecar = parseSidecar(sidecarRaw);

  return (
    <main className="min-h-screen bg-gray-50 dark:bg-[#0a0a0a] pb-12">
      {/* Mobile-friendly Sticky Header */}
      <header className="sticky top-0 z-10 bg-white/80 dark:bg-[#0a0a0a]/80 backdrop-blur-md border-b border-gray-200 dark:border-gray-800 p-4 flex items-center justify-between">
        <Link 
          href="/" 
          className="flex items-center text-blue-600 dark:text-blue-400 hover:text-blue-800 text-sm font-medium"
        >
          <ChevronLeft className="w-5 h-5 mr-1" />
          Back
        </Link>
        <h1 className="font-semibold truncate max-w-[50%]">
          {displayName}
        </h1>
        
        {/* Simple History Dropdown (Using pure CSS/HTML details element for simplicity) */}
        <details className="relative group">
          <summary className="list-none flex items-center cursor-pointer text-sm font-medium text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-gray-800 px-3 py-1.5 rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 transition">
            <History className="w-4 h-4 mr-1.5" />
            {selectedDate}
          </summary>
          <div className="absolute right-0 mt-2 w-48 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 rounded-xl shadow-lg overflow-hidden z-20">
            <div className="max-h-60 overflow-y-auto p-1">
              {reports.map((r) => (
                <Link
                  key={r}
                  href={`/${product}?date=${r}`}
                  className={`block px-4 py-2 text-sm rounded-lg ${
                    r === selectedDate 
                      ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 font-medium' 
                      : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
                  }`}
                >
                  {r}
                </Link>
              ))}
            </div>
          </div>
        </details>
      </header>

      {/* Action toolbar */}
      <div className="px-4 pt-4 max-w-2xl mx-auto w-full flex items-center justify-end gap-3">
        <ScheduleEditorButton product={product} profileYaml={profileYaml} />
        <ColumnChooserButton product={product} profileYaml={profileYaml} />
        <Link
          href={`/onboard?edit=${product}`}
          className="inline-flex items-center justify-center px-4 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-900 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition"
        >
          Edit Profile
        </Link>
        <RunNowButton product={product} lastRun={lastRun} initialRun={initialRun} />
      </div>

      {/* Report Content */}
      <article className="p-4 max-w-2xl mx-auto w-full mt-2">
        <ReportSection>
          {sidecar ? (
            <ResultView data={sidecar} />
          ) : (
            <div className="prose prose-sm sm:prose-base dark:prose-invert max-w-none
              prose-headings:font-semibold
              prose-a:text-blue-600 dark:prose-a:text-blue-400
              prose-table:w-full prose-table:border-collapse
              prose-th:border prose-th:border-gray-200 dark:prose-th:border-gray-800 prose-th:p-2 prose-th:bg-gray-50 dark:prose-th:bg-gray-900
              prose-td:border prose-td:border-gray-200 dark:prose-td:border-gray-800 prose-td:p-2
              prose-tr:border-b prose-tr:border-gray-200 dark:prose-tr:border-gray-800"
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {content}
              </ReactMarkdown>
            </div>
          )}
          {footerInfo && <RunInfoFooter lastRun={footerInfo} />}
        </ReportSection>
      </article>
    </main>
  );
}

