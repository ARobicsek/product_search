import 'server-only';
import { NextRequest } from 'next/server';
import {
  probeUrl,
  type AlterlabOptions,
  type JsonLdListing,
  type ProbeResult,
} from '@/lib/onboard/probe-url';
import { isDetailPreferred } from '@/lib/onboard/detail-preference';
import {
  FORCE_DETAIL_BACKUP_HOSTS,
  PREFER_DETAIL_HOSTS,
} from '@/lib/onboard/vendor-quirks-data';
import { validateProfileDraft } from '@/lib/onboard/validation';

// ADR-115: save-time probe endpoint. The chat route's per-turn budget often
// can't finish probing 8+ vendors plus the ADR-111 detail-URL hunts, so the
// LLM force-finalizes with a weak/partial draft. This endpoint re-runs all
// probes synchronously at save time with a streaming progress UI, then runs
// the ADR-076 deterministic JSON-LD detail-URL backfill. If the budget is hit
// before everything is done, the client shows a "Continue probing" /
// "Save and proceed anyway" choice.

export const runtime = 'nodejs';
export const maxDuration = 60;

// Leave headroom (maxDuration is 60s) for the response to flush and the
// ADR-076 backfill probes (which happen AFTER the probe phase).
const PROBE_BUDGET_MS = 50_000;

// ADR-122: per-URL soft cap. We probe sequentially, fastest-first, so the
// budget reliably lands the quick wins before the slow bot-wall-bypass vendors
// (Scrappey / rendered fetches), and a single stuck AlterLab poll (its own
// internal poll can run ~60s) can't swallow the whole budget and leave EVERY
// vendor "unfinished". A vendor that exceeds this cap is left unprobed and the
// user can "Continue probing" to give it a fresh budget.
const PER_URL_SOFT_CAP_MS = 38_000;
const TIMED_OUT = Symbol('probe_timed_out');

// Lower = probe earlier. A plain fetch (no AlterLab options) is fast; an
// AlterLab render is slower; a Scrappey / skip_alterlab bot-wall bypass is
// slowest. Ordering fastest-first guarantees deterministic progress per pass.
function probeCost(opts: AlterlabOptions | undefined): number {
  if (!opts || Object.keys(opts).length === 0) return 0;
  const o = opts as Record<string, unknown>;
  if (o.use_scrappey || o.skip_alterlab) return 2;
  return 1;
}

interface IncomingBody {
  draft?: unknown;
  /**
   * Optional: URLs the previous /api/onboard/probe call could not finish.
   * When present, only these URLs are re-probed; everything else carries over
   * from the previous result. Lets the user click "Continue probing" without
   * re-paying for already-finished work.
   */
  unprobed?: string[];
  /**
   * Optional: results from the previous /api/onboard/probe call to seed
   * the per-URL state map (so backfill / completion checks see the prior
   * passes). Server still validates each on its own (no trust required).
   */
  priorResults?: Array<{ url: string; ok: boolean; reason: string | null }>;
}

function bad(reason: string, status = 400) {
  return Response.json({ ok: false, error: reason }, { status });
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function hostOf(urlStr: string): string | null {
  try {
    const h = new URL(urlStr).host.toLowerCase();
    return h.startsWith('www.') ? h.slice(4) : h;
  } catch {
    return null;
  }
}

function canonicalUrl(urlStr: string): string {
  try {
    const u = new URL(urlStr);
    return `${u.protocol}//${u.host.toLowerCase()}${u.pathname.replace(/\/+$/, '')}`;
  } catch {
    return urlStr;
  }
}

function getMedian(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const half = Math.floor(sorted.length / 2);
  if (sorted.length % 2 !== 0) return sorted[half];
  return (sorted[half - 1] + sorted[half]) / 2.0;
}

function cleanTokens(s: string): string[] {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, ' ')
    .split(/[\s-]+/)
    .filter((t) => t.length > 0);
}

const STOP_WORDS = new Set([
  'and', 'or', 'with', 'for', 'in', 'of', 'a', 'an', 'the', 'to', 'by', 'from',
  'at', 'on', 'only', 'new', 'used', 'refurbished', 'pack',
]);

const ACCESSORY_WORDS = new Set([
  'accessory', 'accessories', 'battery', 'batteries', 'charger', 'chargers',
  'case', 'cases', 'cable', 'cables', 'part', 'parts', 'filter', 'filters',
  'stand', 'stands', 'attachment', 'attachments', 'mount', 'mounts',
  'brush', 'brushes', 'tool', 'tools', 'bag', 'bags', 'pouch', 'pouches',
  'refill', 'refills', 'replacement', 'replacements', 'strap', 'straps',
  'earpad', 'earpads', 'cushion', 'cushions', 'cover', 'covers', 'kit', 'kits',
]);

interface UniversalSource {
  index: number;
  source: Record<string, unknown>;
  url: string;
  host: string | null;
  pageType: 'search' | 'detail' | undefined;
  alterlabOptions: AlterlabOptions | undefined;
}

function collectUniversalSources(draft: Record<string, unknown>): UniversalSource[] {
  const sources = Array.isArray(draft.sources) ? (draft.sources as unknown[]) : [];
  const out: UniversalSource[] = [];
  for (let i = 0; i < sources.length; i++) {
    const s = sources[i];
    if (!isObject(s)) continue;
    if (s.id !== 'universal_ai_search') continue;
    const url = typeof s.url === 'string' ? s.url.trim() : '';
    if (!url) continue;
    const extra = isObject(s.extra) ? s.extra : null;
    const alterlabOptions = (extra && isObject(extra.alterlab_options))
      ? (extra.alterlab_options as AlterlabOptions)
      : undefined;
    const pageType = (s.page_type === 'detail' || s.page_type === 'search')
      ? (s.page_type as 'search' | 'detail')
      : undefined;
    out.push({
      index: i,
      source: s,
      url,
      host: hostOf(url),
      pageType,
      alterlabOptions,
    });
  }
  return out;
}

function pickPriorPassedListings(
  url: string,
  priorPassResultsByUrl: Map<string, ProbeResult>,
): JsonLdListing[] {
  const prior = priorPassResultsByUrl.get(url);
  return prior?.jsonldListings ?? [];
}

interface PerUrlStatus {
  source: Record<string, unknown>;
  url: string;
  host: string | null;
  pageType: 'search' | 'detail' | undefined;
  result: ProbeResult | null;     // null if not probed (unprobed → continue option)
  probeError: string | null;
}

async function runBackfillForHost(
  send: (p: unknown) => void,
  host: string,
  hostSources: UniversalSource[],
  hostListings: JsonLdListing[],
  displayName: string,
  allowedConditions: Set<string> | null,
  deadlineMs: number,
): Promise<Array<Record<string, unknown>>> {
  if (hostListings.length === 0) {
    send({ type: 'backfill_skip', host, reason: 'no JSON-LD listings on this host\'s search pages' });
    return [];
  }
  const displayNameTokens = cleanTokens(displayName).filter((t) => !STOP_WORDS.has(t));
  const displayNameClean = displayName.toLowerCase();

  send({ type: 'backfill_start', host, candidates: hostListings.length });

  const matchedCandidates = hostListings.filter((c) => {
    if (!c.title || !c.url || typeof c.priceUsd !== 'number') return false;
    const titleTokens = new Set(cleanTokens(c.title));
    if (displayNameTokens.length > 0) {
      const matchCount = displayNameTokens.filter((t) => titleTokens.has(t)).length;
      const matchRatio = matchCount / displayNameTokens.length;
      if (matchRatio < 0.6) return false;
    }
    for (const token of cleanTokens(c.title)) {
      if (ACCESSORY_WORDS.has(token) && !displayNameClean.includes(token)) return false;
    }
    if (allowedConditions) {
      const cond = c.condition ? String(c.condition).toLowerCase() : 'new';
      if (!allowedConditions.has(cond)) return false;
    }
    return true;
  });

  if (matchedCandidates.length === 0) {
    send({ type: 'backfill_skip', host, reason: 'no candidates passed title/condition checks' });
    return [];
  }

  const prices = matchedCandidates.map((c) => c.priceUsd);
  const medianPrice = getMedian(prices);
  const priceCleanedCands = matchedCandidates.filter((c) => c.priceUsd >= medianPrice * 0.5);
  if (priceCleanedCands.length === 0) {
    send({ type: 'backfill_skip', host, reason: 'all candidates filtered by price-band sanity' });
    return [];
  }
  const basePrice = Math.min(...priceCleanedCands.map((c) => c.priceUsd));
  const finalCandidates = priceCleanedCands.filter((c) => c.priceUsd <= basePrice * 1.3);

  const seenUrls = new Set<string>();
  const uniqueCandidates: JsonLdListing[] = [];
  for (const c of finalCandidates) {
    const canonical = canonicalUrl(c.url);
    if (!seenUrls.has(canonical)) {
      seenUrls.add(canonical);
      uniqueCandidates.push(c);
    }
  }
  const keptCandidates = uniqueCandidates.slice(0, 3);

  // Re-use the search source's alterlab options for the detail probe.
  const searchSource = hostSources.find((s) => s.pageType !== 'detail');
  const alterlabOptions = searchSource?.alterlabOptions;

  const added: Array<Record<string, unknown>> = [];
  for (const candidate of keptCandidates) {
    if (Date.now() >= deadlineMs) {
      send({ type: 'backfill_skip', host, reason: 'ran out of time before every detail-page candidate was checked — click “Continue probing” to keep going' });
      break;
    }
    send({ type: 'backfill_probe', host, url: candidate.url, title: candidate.title });
    try {
      const probeRes = await probeUrl(candidate.url, alterlabOptions, 'detail', displayName);
      if (probeRes.ok && probeRes.detailExtractable) {
        const newSource: Record<string, unknown> = {
          id: 'universal_ai_search',
          url: candidate.url,
          page_type: 'detail',
          extra: {
            ...(alterlabOptions ? { alterlab_options: alterlabOptions } : {}),
            page_type: 'detail',
            backfilled_from: 'save_time_probe',
          },
        };
        added.push(newSource);
        send({ type: 'backfill_added', host, url: candidate.url });
      } else {
        send({
          type: 'backfill_failed',
          host,
          url: candidate.url,
          reason: probeRes.reason ?? 'detail not extractable',
        });
      }
    } catch (err) {
      send({
        type: 'backfill_failed',
        host,
        url: candidate.url,
        reason: err instanceof Error ? err.message : String(err),
      });
    }
  }
  send({ type: 'backfill_done', host, added: added.length });
  return added;
}

export async function POST(request: NextRequest) {
  const expected = process.env.WEB_SHARED_SECRET;
  if (!expected) return bad('WEB_SHARED_SECRET not configured on server', 500);
  if (request.headers.get('x-web-secret') !== expected) {
    return bad('invalid or missing x-web-secret header', 401);
  }

  let body: IncomingBody;
  try {
    body = await request.json();
  } catch {
    return bad('invalid JSON body');
  }

  if (!isObject(body.draft)) {
    return bad('draft must be an object');
  }

  const draft = body.draft as Record<string, unknown>;
  const universalSources = collectUniversalSources(draft);
  const displayName = typeof draft.display_name === 'string' ? draft.display_name : '';

  const specFilters = Array.isArray(draft.spec_filters)
    ? (draft.spec_filters as Record<string, unknown>[])
    : [];
  const conditionFilter = specFilters.find((f) => f && f.rule === 'condition_in');
  const allowedConditions = conditionFilter && Array.isArray(conditionFilter.values)
    ? new Set<string>(conditionFilter.values.map(String))
    : null;

  const continueOnlyUrls = Array.isArray(body.unprobed)
    ? new Set(body.unprobed.filter((u): u is string => typeof u === 'string'))
    : null;
  const priorResultsByUrl = new Map<string, ProbeResult>();
  if (Array.isArray(body.priorResults)) {
    for (const pr of body.priorResults) {
      if (!pr || typeof pr.url !== 'string') continue;
      // Synthesize a minimal ProbeResult so the backfill phase still sees it.
      priorResultsByUrl.set(pr.url, {
        ok: pr.ok,
        url: pr.url,
        fetchStatus: null,
        bodyLength: 0,
        jsonldCount: 0,
        anchorCount: 0,
        relevanceHits: 0,
        detailExtractable: null,
        detailTitleMatch: null,
        reason: pr.reason,
      });
    }
  }

  const sseEncode = (payload: unknown): Uint8Array =>
    new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`);

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (payload: unknown) => controller.enqueue(sseEncode(payload));
      const startMs = Date.now();
      const deadlineMs = startMs + PROBE_BUDGET_MS;
      try {
        // ADR-122: which hosts are force_detail_backup but still lack a detail
        // URL — those search URLs must always be (re)probed so the backfill
        // phase below has fresh JSON-LD listings to mine a detail URL from.
        const hostSourcesMap = new Map<string, UniversalSource[]>();
        for (const s of universalSources) {
          if (!s.host) continue;
          if (!hostSourcesMap.has(s.host)) hostSourcesMap.set(s.host, []);
          hostSourcesMap.get(s.host)!.push(s);
        }
        const needsDetailHosts = new Set<string>();
        for (const host of FORCE_DETAIL_BACKUP_HOSTS) {
          const hs = hostSourcesMap.get(host) ?? [];
          if (hs.length === 0) continue;
          const hasSearch = hs.some((x) => x.pageType !== 'detail');
          const hasDetail = hs.some((x) => x.pageType === 'detail');
          if (hasSearch && !hasDetail) needsDetailHosts.add(host);
        }

        // ADR-122: REUSE interview probes. A URL the LLM already confirmed ok
        // during the interview (streamed back as `priorResults`) is not
        // re-probed — that's the "why is it probing AGAIN?" fix. Exception:
        // force_detail_backup hosts still missing a detail URL are re-probed so
        // the backfill can find one. (Continue passes keep the existing
        // `unprobed`-driven behavior.)
        const canReuse = (s: UniversalSource): boolean => {
          if (continueOnlyUrls) return false;
          const prior = priorResultsByUrl.get(s.url);
          if (!prior || !prior.ok) return false;
          if (s.host && needsDetailHosts.has(s.host) && s.pageType !== 'detail') return false;
          return true;
        };
        const reusedSources = universalSources.filter(canReuse);
        const sourcesToProbe = continueOnlyUrls
          ? universalSources.filter((s) => continueOnlyUrls.has(s.url))
          : universalSources.filter((s) => !canReuse(s));

        // ADR-121: emit a plan summary so the modal can show total URLs +
        // capped backfill plan without inferring it from streamed events.
        const hostCounts = new Map<string, number>();
        for (const s of universalSources) {
          if (!s.host) continue;
          hostCounts.set(s.host, (hostCounts.get(s.host) ?? 0) + 1);
        }
        const byHost = Array.from(hostCounts.entries()).map(([host, count]) => ({ host, count }));
        send({
          type: 'plan_summary',
          totalUrls: universalSources.length,
          continueUrls: sourcesToProbe.length,
          byHost,
        });

        send({
          type: 'phase',
          message: continueOnlyUrls
            ? `Continuing probe (${sourcesToProbe.length} URL(s) remaining)…`
            : `Probing ${sourcesToProbe.length} vendor URL(s)…`,
          total: sourcesToProbe.length,
        });

        const perUrl: Map<string, PerUrlStatus> = new Map();
        // Seed perUrl with carried-over status for already-probed URLs.
        for (const s of universalSources) {
          const prior = priorResultsByUrl.get(s.url);
          perUrl.set(s.url, {
            source: s.source,
            url: s.url,
            host: s.host,
            pageType: s.pageType,
            result: prior ?? null,
            probeError: null,
          });
        }

        // ADR-122: mark reused interview probes as done immediately (no
        // network), so the modal shows them ✓ and the user sees we are NOT
        // redundantly re-probing what was already confirmed.
        for (const s of reusedSources) {
          send({
            type: 'url_done',
            url: s.url,
            host: s.host,
            ok: true,
            reason: 'reused — already confirmed while probing during the interview (not re-probed)',
          });
        }

        for (const s of sourcesToProbe) {
          send({ type: 'url_start', url: s.url, host: s.host, pageType: s.pageType ?? 'search' });
        }

        // ADR-122: probe SEQUENTIALLY, fastest-first, each with a per-URL soft
        // cap. The old code raced ALL probes in parallel against one deadline,
        // so a Scrappey-heavy vendor set (each 15-40s) reliably left every
        // vendor "unfinished" — 0 progress, and the loop could only end via
        // "Save anyway". Sequential + fastest-first guarantees the quick wins
        // land first and at least one slow vendor finishes per pass, so
        // "Continue probing" converges instead of spinning.
        const ordered = [...sourcesToProbe].sort(
          (a, b) => probeCost(a.alterlabOptions) - probeCost(b.alterlabOptions),
        );
        for (const s of ordered) {
          const remaining = deadlineMs - Date.now();
          // Not enough time left to meaningfully attempt another vendor — leave
          // the rest unprobed (reported below) rather than starting a probe we
          // can't finish.
          if (remaining <= 2_000) break;
          const cap = Math.min(PER_URL_SOFT_CAP_MS, remaining);
          let capTimer: ReturnType<typeof setTimeout> | null = null;
          const capPromise = new Promise<typeof TIMED_OUT>((resolve) => {
            capTimer = setTimeout(() => resolve(TIMED_OUT), cap);
          });
          try {
            const res = await Promise.race([
              probeUrl(s.url, s.alterlabOptions, s.pageType, displayName),
              capPromise,
            ]);
            if (res === TIMED_OUT) {
              // Left unprobed; surfaced as a deadline row below. Continue to the
              // next vendor (don't break) in case a faster one remains.
              continue;
            }
            const status = perUrl.get(s.url)!;
            status.result = res;
            send({
              type: 'url_done',
              url: s.url,
              host: s.host,
              ok: res.ok,
              reason: res.reason,
              detailExtractable: res.detailExtractable,
              detailTitleMatch: res.detailTitleMatch,
              jsonldCount: res.jsonldCount,
              anchorCount: res.anchorCount,
              relevanceHits: res.relevanceHits,
            });
          } catch (err) {
            const status = perUrl.get(s.url)!;
            const msg = err instanceof Error ? err.message : String(err);
            status.probeError = msg;
            send({ type: 'url_done', url: s.url, host: s.host, ok: false, reason: msg });
          } finally {
            if (capTimer) clearTimeout(capTimer);
          }
        }

        const unprobed: string[] = [];
        for (const s of sourcesToProbe) {
          if (perUrl.get(s.url)?.result == null) {
            unprobed.push(s.url);
            send({ type: 'url_deadline', url: s.url, host: s.host });
          }
        }

        // ADR-076 backfill: for each force_detail_backup host that has a
        // search source but no detail source, mine JSON-LD candidates from
        // its successful search probes and probe up to 3 detail URLs.
        // (`needsDetailHosts`, computed above, is exactly this set.)
        const hostsForBackfill: string[] = Array.from(needsDetailHosts);

        const newDetailSources: Array<Record<string, unknown>> = [];
        for (const host of hostsForBackfill) {
          if (Date.now() >= deadlineMs) {
            send({ type: 'backfill_skip', host, reason: 'ran out of time before the detail-page search could run — click “Continue probing” to keep going' });
            continue;
          }
          const hostSources = hostSourcesMap.get(host) ?? [];
          // Aggregate JSON-LD listings from this host's successful SEARCH probes.
          const listings: JsonLdListing[] = [];
          for (const hs of hostSources) {
            if (hs.pageType === 'detail') continue;
            const status = perUrl.get(hs.url);
            if (status?.result?.ok && status.result.jsonldListings) {
              listings.push(...status.result.jsonldListings);
            } else {
              // Fall back to carried-over priorResults' listings if any.
              listings.push(...pickPriorPassedListings(hs.url, priorResultsByUrl));
            }
          }
          const added = await runBackfillForHost(
            send,
            host,
            hostSources,
            listings,
            displayName,
            allowedConditions,
            deadlineMs,
          );
          newDetailSources.push(...added);
        }

        // Build enriched draft: keep failing-but-detail-preferred sources with
        // a note (advisory), demote other failures to sources_pending, append
        // newly-found detail URLs.
        const sourcesIn = Array.isArray(draft.sources) ? (draft.sources as unknown[]) : [];
        const pendingIn = Array.isArray(draft.sources_pending)
          ? (draft.sources_pending as unknown[])
          : [];

        const sourcesOut: unknown[] = [];
        const newPending: unknown[] = [...pendingIn];
        for (const s of sourcesIn) {
          if (!isObject(s) || s.id !== 'universal_ai_search') {
            sourcesOut.push(s);
            continue;
          }
          const url = typeof s.url === 'string' ? s.url.trim() : '';
          if (!url) {
            sourcesOut.push(s);
            continue;
          }
          const status = perUrl.get(url);
          if (!status || status.result == null) {
            // Unprobed (budget): keep as-is, mark unfinished.
            sourcesOut.push(s);
            continue;
          }
          const result = status.result;
          if (result.ok) {
            sourcesOut.push(s);
            continue;
          }
          const reason = result.reason ?? status.probeError ?? 'probe returned 0 candidates';
          if (isDetailPreferred(url, status.pageType, FORCE_DETAIL_BACKUP_HOSTS, PREFER_DETAIL_HOSTS)) {
            sourcesOut.push({
              ...s,
              note: `probe was weak at save time (advisory — kept; runtime will retry): ${reason}`,
            });
            continue;
          }
          newPending.push({
            ...s,
            note: `probe returned 0 candidates: ${reason}`,
          });
        }
        for (const d of newDetailSources) sourcesOut.push(d);

        const enrichedDraft = {
          ...draft,
          sources: sourcesOut,
          sources_pending: newPending,
        };

        // Re-validate so the client knows whether the gate will pass on save.
        const validation = validateProfileDraft(enrichedDraft, null, null);

        // ADR-121: detect a zero-progress Continue pass. If we were called with
        // `unprobed` (Continue mode) and the new `unprobed` set is the same
        // size or larger, no URL finished this attempt — the loop is stuck
        // and the client should stop offering plain "Continue probing."
        const noProgress = continueOnlyUrls != null
          && sourcesToProbe.length > 0
          && unprobed.length >= sourcesToProbe.length;

        send({
          type: 'done',
          enrichedDraft,
          complete: unprobed.length === 0,
          unprobed,
          noProgress,
          elapsedMs: Date.now() - startMs,
          validation: {
            ok: validation.ok,
            // Technical text drives the client's bypassable-violation detection
            // (it regex-matches ADR markers); userErrors/userWarnings (ADR-123)
            // are what the modal renders to the user.
            errors: validation.errors,
            warnings: validation.warnings,
            userErrors: validation.userErrors,
            userWarnings: validation.userWarnings,
          },
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'probe stream failed';
        send({ type: 'error', error: message });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
    },
  });
}
