import Link from 'next/link';
import { Plus } from 'lucide-react';
import {
  getProducts,
  getProductReports,
  getReportContent,
  getReportJsonSidecar,
  getLastRunInstant,
  getProductProfileContent,
} from '@/lib/github';
import { getActiveRuns } from '@/lib/dispatch';
import { readScheduleFromYaml } from '@/lib/schedule';
import { parseSidecar, type ReportSidecar, type ReportSidecarV1, type ReportSidecarV2 } from '@/app/[product]/result-types';
import { DeleteProductModal } from '@/components/DeleteProductModal';
import { CardRunStatus } from './CardRunStatus';
import AlertsBell from './AlertsBell';

// Running state is live and must never be served from an edge/RSC cache, same
// reasoning as the product detail page.
export const dynamic = 'force-dynamic';

// Strip inline markdown (bold/italic/code) so a legacy markdown summary reads
// as plain prose on the card instead of leaking `**` etc.
function stripMarkdown(text: string): string {
  return text.replace(/\*\*/g, '').replace(/[*_`]/g, '').trim();
}

// Fallback summary for reports written before the ADR-096 JSON sidecar: the
// first non-heading, non-table, non-list line of the markdown.
function extractBottomLine(markdown: string): string {
  const lines = markdown.split('\n');
  for (const line of lines) {
    const trimmed = stripMarkdown(line);
    if (trimmed && !trimmed.startsWith('#') && !trimmed.startsWith('|') && !trimmed.startsWith('-')) {
      return trimmed.substring(0, 150) + (trimmed.length > 150 ? '...' : '');
    }
  }
  return 'No summary available.';
}

// Top-level `display_name:` from a profile.yaml, for products that have no
// JSON sidecar yet (no report run). Regex mirrors the lightweight reader in
// lib/schedule.ts rather than pulling in a YAML parse just for one scalar.
function displayNameFromProfile(yamlText: string | null): string | null {
  if (!yamlText) return null;
  const m = yamlText.match(/^display_name:[ \t]*(.+?)[ \t]*\r?$/m);
  if (!m) return null;
  return m[1].replace(/^["']|["']$/g, '').trim() || null;
}

function prettifySlug(slug: string): string {
  return slug.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function fmtPrice(n: number | null): string | null {
  if (n === null || n === undefined) return null;
  return `$${n.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem.toString().padStart(2, '0')}s`;
}

function formatCost(usd: number): string {
  if (usd === 0) return '$0.00';
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

// Short, friendly label for the ai_filter step's model. Local models
// (qwen-coder, qwen3.6-27b-mtp) show as-is; Anthropic Haiku is prettified.
// Returns null when no LLM filter ran (e.g. variant_strict skips the filter,
// so there's no ai_filter step in run_cost) — the card then shows nothing.
function formatFilterModel(model: string | null | undefined): string | null {
  if (!model) return null;
  if (/haiku/i.test(model)) return 'Haiku 4.5';
  return model;
}

// Later of two ISO instants (either may be null). Used to reconcile the
// data-CSV run instant with the report sidecar's generated_at: a zero-pass
// run writes a report (and sidecar) but no data CSV, so the CSV alone would
// report a stale older run as the latest.
function laterIso(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a) >= Date.parse(b) ? a : b;
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

      // Prefer the structured JSON sidecar (ADR-096) for the card summary;
      // fall back to the markdown's first prose line for legacy reports.
      let sidecar: ReportSidecar | null = null;
      let fallbackSummary = 'No reports yet.';
      if (latestDate) {
        const [rawSidecar, content] = await Promise.all([
          getReportJsonSidecar(product, latestDate),
          getReportContent(product, latestDate),
        ]);
        sidecar = parseSidecar(rawSidecar);
        fallbackSummary = content
          ? extractBottomLine(content)
          : 'No summary available.';
      }

      const isV1 = sidecar?.schema_version === 1;
      const title =
        (sidecar ? (isV1 ? (sidecar as ReportSidecarV1).product.display_name : (sidecar as ReportSidecarV2).display_name) : null) ||
        displayNameFromProfile(profile) ||
        prettifySlug(product);

      // Listings are price-ranked, so listings[0] is the cheapest passing
      // option. total_passed is the full count (listings[] may be capped).
      const cheapest = sidecar?.listings?.[0] ?? null;
      const priceLabel = cheapest ? fmtPrice(cheapest.price_usd) : null;
      const listingCount = sidecar ? (isV1 ? (sidecar as ReportSidecarV1).listings_meta.total_passed : (sidecar as ReportSidecarV2).survivor_count) : null;

      // Run duration = report build time (sidecar.generated_at) minus run
      // start (the data-CSV instant). Both UTC. A hand-built or stale sidecar
      // can be paired with an unrelated CSV, so cap at 30 min — real runs are
      // bounded well under that by the wall-budget; anything larger is omitted.
      let durationMs: number | null = null;
      if (sidecar?.generated_at && lastRunIso) {
        const d =
          new Date(sidecar.generated_at).getTime() -
          new Date(lastRunIso).getTime();
        if (d > 0 && d < 30 * 60 * 1000) durationMs = d;
      }

      // The card's "last run" time: prefer whichever is newer, the data-CSV
      // instant or the latest report's build time. (lastRunIso above stays the
      // raw CSV instant — it's the run START used for the duration math.)
      const lastRunDisplayIso = laterIso(lastRunIso, sidecar?.generated_at ?? null);

      let costUsd: number | null = null;
      if (sidecar?.run_cost && typeof sidecar.run_cost.total_usd === 'number') {
        costUsd = sidecar.run_cost.total_usd;
      }

      // Which LLM did the relevance filter this run (ai_filter step). Absent
      // when no LLM ran (variant_strict skip) → no label on the card.
      const filterStep = sidecar?.run_cost?.steps?.find((s) => s.step === 'ai_filter');
      const filterModel = formatFilterModel(filterStep?.model);

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
        title,
        latestDate,
        lastRunIso: lastRunDisplayIso,
        status,
        runningSinceIso,
        priceLabel,
        listingCount,
        durationMs,
        costUsd,
        filterModel,
        fallbackSummary,
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
              className="relative group block p-5 bg-white dark:bg-gray-950 rounded-xl shadow-sm border border-gray-100 dark:border-gray-800 hover:shadow-md transition-shadow"
            >
              <div className="flex items-start justify-between gap-3">
                <h2 className="text-lg font-semibold leading-snug min-w-0 flex-1 break-words">
                  <Link
                    href={`/${data.product}`}
                    className="focus:outline-none rounded focus:ring-2 focus:ring-blue-500 before:absolute before:inset-0 z-0"
                  >
                    {data.title}
                  </Link>
                </h2>
                <div className="shrink-0 -mr-2 relative z-10">
                  <DeleteProductModal productSlug={data.product} webSecret={process.env.WEB_SHARED_SECRET || ''} />
                </div>
              </div>
              <div className="mt-2 relative z-10">
                <CardRunStatus
                  lastRunIso={data.lastRunIso}
                  fallbackDate={data.latestDate}
                  status={data.status}
                  runningSinceIso={data.runningSinceIso}
                />
              </div>
              <p className="mt-2 text-sm text-gray-600 dark:text-gray-400 line-clamp-2 relative z-10 pointer-events-none">
                {data.listingCount === 0 ? (
                  <>
                    No passing listings
                    {data.durationMs !== null && ` · took ${formatDuration(data.durationMs)}`}
                    {data.costUsd !== null && ` · ${formatCost(data.costUsd)}`}
                    {data.filterModel && ` · filter: ${data.filterModel}`}
                  </>
                ) : data.listingCount !== null ? (
                  <>
                    {data.priceLabel && (
                      <span className="font-semibold text-gray-900 dark:text-gray-100">
                        {data.priceLabel}
                      </span>
                    )}
                    {data.priceLabel && ' · '}
                    {data.listingCount} listing{data.listingCount === 1 ? '' : 's'}
                    {data.durationMs !== null && ` · took ${formatDuration(data.durationMs)}`}
                    {data.costUsd !== null && ` · ${formatCost(data.costUsd)}`}
                    {data.filterModel && ` · filter: ${data.filterModel}`}
                  </>
                ) : (
                  data.fallbackSummary
                )}
              </p>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
