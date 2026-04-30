import Link from 'next/link';
import {
  getProductProfileContent,
  getProductProfileExists,
  getProductReports,
  getReportContent,
} from '@/lib/github';
import { getLastCompletedRun } from '@/lib/dispatch';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronLeft, History, Sparkles } from 'lucide-react';
import { notFound } from 'next/navigation';
import { RunNowButton } from './RunNowButton';
import SubscribeButton from './SubscribeButton';
import { ColumnChooserButton } from './ColumnChooserButton';

export default async function ProductPage({
  params,
  searchParams,
}: {
  params: Promise<{ product: string }>;
  searchParams: Promise<{ date?: string }>;
}) {
  const { product } = await params;
  const { date } = await searchParams;

  const reports = await getProductReports(product);
  const lastRun = await getLastCompletedRun(product).catch(() => null);
  // Profile YAML powers the inline column chooser. Fetched in parallel-ish
  // (await above already kicked off `reports`) so the page render isn't
  // serialized.
  const profileYaml = await getProductProfileContent(product).catch(() => null);

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
          <h1 className="font-semibold capitalize truncate max-w-[50%]">
            {product.replace(/-/g, ' ')}
          </h1>
          <span className="w-12" aria-hidden />
        </header>

        <div className="px-4 pt-4 max-w-2xl mx-auto w-full flex items-center justify-end gap-3">
          <SubscribeButton productSlug={product} />
          <ColumnChooserButton product={product} profileYaml={profileYaml} />
          <Link
            href={`/onboard?edit=${product}`}
            className="inline-flex items-center justify-center px-4 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-900 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition"
          >
            Edit Profile
          </Link>
          <RunNowButton product={product} lastRun={lastRun} />
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
  const content = await getReportContent(product, selectedDate);

  if (!content) {
    return notFound();
  }

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
        <h1 className="font-semibold capitalize truncate max-w-[50%]">
          {product.replace(/-/g, ' ')}
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
        <SubscribeButton productSlug={product} />
        <ColumnChooserButton product={product} profileYaml={profileYaml} />
        <Link
          href={`/onboard?edit=${product}`}
          className="inline-flex items-center justify-center px-4 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-900 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition"
        >
          Edit Profile
        </Link>
        <RunNowButton product={product} lastRun={lastRun} />
      </div>

      {/* Report Content */}
      <article className="p-4 max-w-2xl mx-auto w-full mt-2">
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
        {lastRun && <RunInfoFooter lastRun={lastRun} />}
      </article>
    </main>
  );
}

function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem.toString().padStart(2, '0')}s`;
}

function RunInfoFooter({
  lastRun,
}: {
  lastRun: { completedAt: string; durationMs: number; conclusion: string | null };
}) {
  const completed = new Date(lastRun.completedAt);
  const completedLabel = completed.toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
  const duration = formatDuration(lastRun.durationMs);
  const failed = lastRun.conclusion && lastRun.conclusion !== 'success';
  return (
    <div
      className={`mt-6 pt-4 border-t border-gray-200 dark:border-gray-800 text-xs ${
        failed ? 'text-red-600 dark:text-red-400' : 'text-gray-500 dark:text-gray-400'
      }`}
    >
      Last run completed {completedLabel} · took {duration}
      {failed ? ` · conclusion: ${lastRun.conclusion}` : ''}
    </div>
  );
}
