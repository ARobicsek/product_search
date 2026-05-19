import Link from 'next/link';
import { Plus } from 'lucide-react';
import {
  getProducts,
  getProductReports,
  getReportContent,
  getLastRunInstant,
  getProductProfileContent,
} from '@/lib/github';
import { getActiveRuns } from '@/lib/dispatch';
import { readScheduleFromYaml } from '@/lib/schedule';
import { DeleteProductModal } from '@/components/DeleteProductModal';
import { CardRunStatus } from './CardRunStatus';
import AlertsBell from './AlertsBell';

// Running state is live and must never be served from an edge/RSC cache, same
// reasoning as the product detail page.
export const dynamic = 'force-dynamic';

// Helper to extract a summary from markdown (e.g., first non-heading line)
function extractBottomLine(markdown: string) {
  const lines = markdown.split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed && !trimmed.startsWith('#') && !trimmed.startsWith('|') && !trimmed.startsWith('-')) {
      return trimmed.substring(0, 150) + (trimmed.length > 150 ? '...' : '');
    }
  }
  return 'No summary available.';
}

export default async function Home() {
  const products = await getProducts();

  const activeRuns = await getActiveRuns().catch(() => ({
    onDemand: [] as { title: string; startedIso: string | null }[],
    scheduledTickActive: false,
    scheduledTickStartedIso: null as string | null,
  }));

  const productData = await Promise.all(
    products.map(async (product) => {
      const [reports, lastRunIso, profile] = await Promise.all([
        getProductReports(product),
        getLastRunInstant(product),
        getProductProfileContent(product).catch(() => null),
      ]);
      const latestDate = reports.length > 0 ? reports[0] : null;
      let summary = 'No reports yet.';

      if (latestDate) {
        const content = await getReportContent(product, latestDate);
        if (content) {
          summary = extractBottomLine(content);
        }
      }

      // An on-demand run carries the slug in its title (so we know its exact
      // start). A scheduler-tick has no per-product title, so it's attributed
      // to any product declaring a schedule block — best-effort, since one
      // tick processes all due products together.
      const hasSchedule =
        !!profile && readScheduleFromYaml(profile) !== null;
      const onDemandMatch = activeRuns.onDemand.find((r) =>
        r.title.includes(product),
      );

      let status: 'running' | 'waiting' | 'idle' = 'idle';
      let runningSinceIso: string | null = null;
      if (onDemandMatch) {
        status = 'running';
        runningSinceIso = onDemandMatch.startedIso;
      } else if (activeRuns.scheduledTickActive && hasSchedule) {
        status = 'running';
        runningSinceIso = activeRuns.scheduledTickStartedIso;
      } else if (hasSchedule) {
        status = 'waiting';
      }

      return {
        product,
        latestDate,
        lastRunIso,
        status,
        runningSinceIso,
        summary,
      };
    })
  );

  return (
    <main className="p-4 max-w-2xl mx-auto w-full">
      <header className="mb-8 mt-4 flex items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Product Search</h1>
          <p className="text-gray-500 dark:text-gray-400 mt-2">Daily price tracking reports</p>
        </div>
        <div className="shrink-0 mt-1 flex items-center gap-2">
          <AlertsBell />
          <Link
            href="/onboard"
            className="flex items-center text-sm font-medium px-3 py-1.5 rounded-full bg-blue-600 text-white hover:bg-blue-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <Plus className="w-4 h-4 mr-1" />
            New
          </Link>
        </div>
      </header>

      {productData.length === 0 ? (
        <div className="p-6 bg-gray-50 dark:bg-gray-900 rounded-xl text-center border border-gray-100 dark:border-gray-800">
          <p className="text-gray-500">No products found.</p>
        </div>
      ) : (
        <div className="grid gap-4">
          {productData.map((data) => (
            <div
              key={data.product}
              className="relative group block p-6 bg-white dark:bg-gray-950 rounded-xl shadow-sm border border-gray-100 dark:border-gray-800 hover:shadow-md transition-shadow"
            >
              <div className="flex justify-between items-start mb-3">
                <h2 className="text-xl font-semibold capitalize">
                  <Link 
                    href={`/${data.product}`}
                    className="focus:outline-none rounded focus:ring-2 focus:ring-blue-500 before:absolute before:inset-0 z-0"
                  >
                    {data.product.replace(/-/g, ' ')}
                  </Link>
                </h2>
                <div className="flex items-center gap-2 relative z-10">
                  <CardRunStatus
                    lastRunIso={data.lastRunIso}
                    fallbackDate={data.latestDate}
                    status={data.status}
                    runningSinceIso={data.runningSinceIso}
                  />
                  <div className="-mr-2">
                    <DeleteProductModal productSlug={data.product} webSecret={process.env.WEB_SHARED_SECRET || ''} />
                  </div>
                </div>
              </div>
              <p className="text-gray-600 dark:text-gray-400 text-sm line-clamp-2 relative z-10 pointer-events-none">
                {data.summary}
              </p>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
