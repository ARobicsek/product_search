'use client';

/**
 * The post-run report view (ADR-096).
 *
 * Reads the structured JSON sidecar emitted by the worker and renders
 * a stack of equal-sized listing cards (price-ranked, no winner
 * elevation), a Sources panel with honest classifier-derived status
 * pills, and the Run-cost table. The legacy markdown renderer is only
 * used for historical reports that pre-date this commit.
 */

import { useState } from 'react';
import type {
  Badge,
  PendingSource,
  ReportSidecar,
  ResultListing,
  ResultSource,
  RunCost,
  Severity,
  SourceStatus,
  ReportSidecarV1,
  ReportSidecarV2,
} from './result-types';

const SEVERITY_PILL: Record<Severity, string> = {
  info: 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300',
  warning:
    'bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 ring-1 ring-amber-200 dark:ring-amber-900',
  danger:
    'bg-rose-50 dark:bg-rose-950/40 text-rose-800 dark:text-rose-300 ring-1 ring-rose-200 dark:ring-rose-900',
};

const STATUS_PILL: Record<SourceStatus, string> = {
  ok: 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 ring-1 ring-emerald-200 dark:ring-emerald-900',
  no_match:
    'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 ring-1 ring-gray-200 dark:ring-gray-700',
  empty_page:
    'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 ring-1 ring-gray-200 dark:ring-gray-700',
  parser_gap:
    'bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 ring-1 ring-amber-200 dark:ring-amber-900',
  transient:
    'bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 ring-1 ring-amber-200 dark:ring-amber-900',
  permanent:
    'bg-rose-50 dark:bg-rose-950/40 text-rose-800 dark:text-rose-300 ring-1 ring-rose-200 dark:ring-rose-900',
  // ADR-099: calm, informational — not an error. The vendor just isn't
  // stocking the product yet; we spent ~$0 confirming it and will auto-wake.
  watched:
    'bg-sky-50 dark:bg-sky-950/40 text-sky-800 dark:text-sky-300 ring-1 ring-sky-200 dark:ring-sky-900',
};

function fmtMoney(n: number | null, fx?: string | null): string {
  if (n === null || n === undefined) return 'unknown';
  const base = `$${n.toFixed(2)}`;
  if (!fx) return base;
  if (typeof fx === 'string' && fx !== 'True') return `${base} (${fx})`;
  return `${base} (fx)`;
}

function fmtMoneyExact(n: number | null): string {
  if (n === null || n === undefined) return '—';
  return `$${n.toFixed(4)}`;
}

/**
 * Strip markdown formatting (bold ``**…**``, italic ``_…_``) from a
 * classifier-produced reason so it reads as plain prose inside a card.
 * The classifier emits markdown because its other consumer is the
 * markdown callout; here we render it inline.
 */
function stripMd(text: string): string {
  return text
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/(^|\s)_([^_]+)_(?=\s|$|[.,;:!?])/g, '$1$2');
}

function Pill({
  label,
  className,
}: {
  label: string;
  className: string;
}) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${className}`}
    >
      {label}
    </span>
  );
}

function BadgeRow({ badges }: { badges: Badge[] }) {
  if (!badges?.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mt-2">
      {badges.map((b) => (
        <Pill key={b.key} label={b.label} className={SEVERITY_PILL[b.severity]} />
      ))}
    </div>
  );
}

function VendorFavicon({ host }: { host: string | null }) {
  if (!host) return null;
  const cleanHost = host.replace(/^www\./, '');
  // Google's favicon service is free and stable; falls back gracefully
  // (returns a globe icon) if the vendor has no favicon.
  const src = `https://www.google.com/s2/favicons?domain=${cleanHost}&sz=64`;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={20}
      height={20}
      className="rounded shrink-0"
    />
  );
}

function ListingCard({ listing }: { listing: ResultListing }) {
  const host = listing.vendor_host ?? listing.source;
  const cleanHost = host.replace(/^www\./, '');
  const showTotal =
    listing.total_for_target_usd !== null &&
    listing.total_for_target_usd !== listing.price_usd;

  return (
    <article
      className="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 p-4 sm:p-5 space-y-2"
      aria-label={`Listing rank ${listing.rank}: ${listing.title}`}
    >
      <header className="flex items-center justify-between gap-3 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <VendorFavicon host={listing.vendor_host} />
          <span className="text-sm font-medium text-gray-700 dark:text-gray-300 truncate">
            {cleanHost}
          </span>
        </div>
        <span className="text-xs text-gray-500 dark:text-gray-400 shrink-0">
          #{listing.rank}
        </span>
      </header>

      <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100 break-words">
        {listing.title}
      </h3>

      <div className="flex items-baseline gap-3 flex-wrap">
        <span className="text-2xl font-bold text-gray-900 dark:text-gray-100 tabular-nums">
          {fmtMoney(listing.price_usd, listing.currency_approx_fx)}
        </span>
        {showTotal && (
          <span className="text-sm text-gray-500 dark:text-gray-400 tabular-nums">
            Total: {fmtMoney(listing.total_for_target_usd, listing.currency_approx_fx)}
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
        {listing.condition && (
          <span className="capitalize">{listing.condition}</span>
        )}
        {listing.seller_name &&
          listing.seller_name.replace(/^www\./, '') !== cleanHost && (
            <span>Seller: {listing.seller_name}</span>
          )}
        {listing.is_kit && listing.kit_module_count > 1 && (
          <span>{listing.kit_module_count}-pack</span>
        )}
      </div>

      <BadgeRow badges={listing.badges} />

      <div className="pt-2">
        <a
          href={listing.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-sm font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 break-all"
        >
          View on {cleanHost}
          <span aria-hidden>→</span>
        </a>
      </div>
    </article>
  );
}

function SourcesPanel({
  sources,
  pending,
}: {
  sources: ResultSource[];
  pending: PendingSource[];
}) {
  if (!sources?.length && !pending?.length) return null;
  return (
    <section className="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 p-4 sm:p-5">
      <h2 className="text-base font-semibold mb-3 text-gray-900 dark:text-gray-100">
        Sources searched
      </h2>
      <ul className="divide-y divide-gray-100 dark:divide-gray-800">
        {sources?.map((s) => (
          <li key={s.label} className="py-2.5 first:pt-0 last:pb-0">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <span className="text-sm font-medium text-gray-900 dark:text-gray-100 break-all">
                {s.label}
              </span>
              <div className="flex items-center gap-2 text-xs">
                <Pill label={s.status_label} className={STATUS_PILL[s.status]} />
                <span className="text-gray-500 dark:text-gray-400 tabular-nums">
                  {s.fetched} fetched · {s.passed} passed
                </span>
              </div>
            </div>
            {s.status !== 'ok' && s.reason && (
              <p className="text-xs text-gray-600 dark:text-gray-400 mt-1.5 leading-relaxed">
                {stripMd(s.reason)}
              </p>
            )}
            {s.scrappey_attempts && s.scrappey_attempts.length > 0 && (
              <div className="mt-2 space-y-1.5">
                {s.scrappey_attempts.map((attempt, i) => (
                  <div key={i} className="text-[11px] bg-gray-50 dark:bg-gray-900/50 rounded px-2 py-1.5 text-gray-600 dark:text-gray-400 border border-gray-100 dark:border-gray-800">
                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                      <span className="font-medium text-gray-700 dark:text-gray-300">Scrappey Attempt {i + 1}</span>
                      <span>{attempt.elapsed_ms}ms</span>
                      <span>Status: {attempt.origin_status}</span>
                      <span>Len: {attempt.body_len}</span>
                      {attempt.cf_challenge && <span className="text-amber-600 dark:text-amber-500 font-medium">CF Challenge</span>}
                    </div>
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-0.5 text-gray-500 dark:text-gray-500">
                      <span>Trigger: {attempt.triggered_by}</span>
                      <span className="truncate max-w-[200px] sm:max-w-xs">IP: {attempt.exit_ip} ({attempt.exit_country})</span>
                      {attempt.exit_hosting && <span>Hosting IP</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>
      {pending?.length > 0 && (
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-3 pt-3 border-t border-gray-100 dark:border-gray-800">
          <span className="font-medium">Pending (not yet wired):</span>{' '}
          {pending.map((p) => p.label).join(', ')}
        </p>
      )}
    </section>
  );
}

function OutcomePanel({ outcome }: { outcome: ReportSidecarV2['outcome'] }) {
  const isOk = outcome.class === 'ok';
  const pillColor = isOk
    ? 'bg-emerald-50 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 ring-1 ring-emerald-200 dark:ring-emerald-900'
    : 'bg-amber-50 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 ring-1 ring-amber-200 dark:ring-amber-900';
  
  return (
    <section className="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 p-4 sm:p-5">
      <h2 className="text-base font-semibold mb-3 text-gray-900 dark:text-gray-100">
        Run Outcome
      </h2>
      <div className="space-y-3">
        <div className="flex items-center gap-2 text-xs">
          <Pill label={outcome.class} className={pillColor} />
          {outcome.message && (
            <span className="text-gray-700 dark:text-gray-300">
              {outcome.message}
            </span>
          )}
        </div>
        {outcome.notes?.length > 0 && (
          <ul className="text-xs text-gray-500 dark:text-gray-400 space-y-1">
            {outcome.notes.map((note, i) => (
              <li key={i}>
                <span className="font-medium">{note.class}:</span> {note.message}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function RunCostTable({ runCost }: { runCost: RunCost }) {
  if (!runCost?.steps?.length) return null;
  return (
    <section className="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 p-4 sm:p-5">
      <h2 className="text-base font-semibold mb-3 text-gray-900 dark:text-gray-100">
        Run cost
      </h2>
      <div className="overflow-x-auto -mx-4 sm:mx-0">
        <table className="w-full text-xs sm:text-sm">
          <thead>
            <tr className="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-800">
              <th className="py-2 pl-4 sm:pl-0 pr-2 font-medium">Step</th>
              <th className="py-2 px-2 font-medium hidden sm:table-cell">Model</th>
              <th className="py-2 px-2 font-medium text-right tabular-nums">In</th>
              <th className="py-2 px-2 font-medium text-right tabular-nums">Out</th>
              <th className="py-2 pl-2 pr-4 sm:pr-0 font-medium text-right tabular-nums">
                Cost
              </th>
            </tr>
          </thead>
          <tbody className="text-gray-700 dark:text-gray-300">
            {runCost.steps.map((s, i) => (
              <tr
                key={`${s.step}-${i}`}
                className="border-b border-gray-100 dark:border-gray-800/50 last:border-0"
              >
                <td className="py-2 pl-4 sm:pl-0 pr-2 break-all">{s.step}</td>
                <td className="py-2 px-2 hidden sm:table-cell text-gray-500 dark:text-gray-400 break-all">
                  {s.model}
                </td>
                <td className="py-2 px-2 text-right tabular-nums">
                  {s.input_tokens.toLocaleString()}
                </td>
                <td className="py-2 px-2 text-right tabular-nums">
                  {s.output_tokens.toLocaleString()}
                </td>
                <td className="py-2 pl-2 pr-4 sm:pr-0 text-right tabular-nums">
                  {fmtMoneyExact(s.cost_usd)}
                </td>
              </tr>
            ))}
            <tr className="font-semibold text-gray-900 dark:text-gray-100">
              <td className="py-2 pl-4 sm:pl-0 pr-2">Total</td>
              <td className="py-2 px-2 hidden sm:table-cell"></td>
              <td className="py-2 px-2"></td>
              <td className="py-2 px-2"></td>
              <td className="py-2 pl-2 pr-4 sm:pr-0 text-right tabular-nums">
                {fmtMoneyExact(runCost.total_usd)}
                {runCost.any_unpriced ? '*' : ''}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-2 leading-relaxed">
        Costs are estimates from a hand-maintained price table; actual billing
        may differ.
        {runCost.any_unpriced && ' * Total excludes unpriced calls.'}
      </p>
    </section>
  );
}

export function ResultView({ data }: { data: ReportSidecar }) {
  const [showAll, setShowAll] = useState(false);
  const hasListings = data.listings.length > 0;
  const isV1 = data.schema_version === 1;
  const v1 = data as ReportSidecarV1;
  const v2 = data as ReportSidecarV2;

  // Progressive disclosure: if all_listings is available and larger than
  // the capped display set, let the user expand to see every survivor.
  const allListings = !isV1 && v2.all_listings?.length ? v2.all_listings : null;
  const hasMore = allListings !== null && allListings.length > data.listings.length;
  const visibleListings = showAll && allListings ? allListings : data.listings;

  return (
    <div className="space-y-4">
      {hasListings ? (
        <section className="space-y-3">
          {visibleListings.map((lst) => (
            <ListingCard key={`${lst.rank}-${lst.url}`} listing={lst} />
          ))}
          {isV1 && v1.listings_meta.total_passed > v1.listings_meta.shown && (
            <p className="text-xs text-gray-500 dark:text-gray-400 text-center">
              Showing the top {v1.listings_meta.shown} of{' '}
              {v1.listings_meta.total_passed} passing listings.
            </p>
          )}
          {!isV1 && (
            <div className="space-y-3 pt-2">
              {hasMore && !showAll && (
                <button
                  onClick={() => setShowAll(true)}
                  className="w-full py-2.5 text-sm font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 bg-blue-50/50 dark:bg-blue-950/20 hover:bg-blue-50 dark:hover:bg-blue-950/40 border border-blue-200 dark:border-blue-900 rounded-xl transition-colors cursor-pointer"
                >
                  Show all {allListings!.length} listings
                  <span className="text-gray-500 dark:text-gray-400 font-normal">
                    {' '}(showing top {data.listings.length})
                  </span>
                </button>
              )}
              {showAll && hasMore && (
                <button
                  onClick={() => setShowAll(false)}
                  className="w-full py-2.5 text-sm font-medium text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 bg-gray-50/50 dark:bg-gray-900/20 hover:bg-gray-100 dark:hover:bg-gray-800/40 border border-gray-200 dark:border-gray-800 rounded-xl transition-colors cursor-pointer"
                >
                  Show top {data.listings.length} only
                </button>
              )}
              <p className="text-xs text-gray-500 dark:text-gray-400 text-center">
                {visibleListings.length === v2.survivor_count
                  ? `Showing all ${v2.survivor_count} matched listings (${v2.recall_count} found).`
                  : `Showing ${visibleListings.length} of ${v2.survivor_count} matched listings (${v2.recall_count} found).`}
              </p>
            </div>
          )}
        </section>
      ) : (
        <section className="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 p-6 text-center">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            No listings passed the filter this run. {isV1 ? "The Sources panel below shows what was tried and why each came back empty." : ""}
          </p>
        </section>
      )}

      {isV1 ? (
        <SourcesPanel sources={v1.sources} pending={v1.sources_pending} />
      ) : (
        <OutcomePanel outcome={v2.outcome} />
      )}
      <RunCostTable runCost={data.run_cost} />
    </div>
  );
}
