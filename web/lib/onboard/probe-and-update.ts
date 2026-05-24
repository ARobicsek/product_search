import 'server-only';

import { gateUniversalAiUrls, type ProbeReport } from '@/lib/onboard/gate-universal-ai';
import { renderProfileYaml } from '@/lib/onboard/render-yaml';
import { commitNewProfile } from '@/lib/onboard/commit';
import { FORCE_DETAIL_BACKUP_HOSTS } from '@/lib/onboard/vendor-quirks-data';
import { probeUrl, type JsonLdListing, type AlterlabOptions } from '@/lib/onboard/probe-url';

// Background probe-and-update: runs AFTER the initial save response has been
// sent to the client (via waitUntil). Probes every universal_ai_search URL
// in the draft; if any are demoted to sources_pending, commits an updated
// profile.yaml so the on-disk version is already cleaned up before the next
// worker run.
//
// ADR-076: It also performs an active, deterministic backfill pass. For every
// force_detail_backup host that has search URLs but no detail URLs, it extracts
// candidate detail URLs from the search JSON-LD, filters out accessories and non-matching
// models, applies price-band/median sanity checks, probes them, and appends them
// as page_type: "detail" sources.
//
// This is best-effort — if it fails (network blip, GitHub conflict), the
// worst case is the profile carries URLs that will yield 0 listings on the
// next run. The user can always re-save or manually edit.

export interface ProbeAndUpdateResult {
  probed: boolean;
  reports: ProbeReport[];
  demotions: number;
  followUpCommit: boolean;
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
  if (sorted.length % 2 !== 0) {
    return sorted[half];
  }
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
  'and', 'or', 'with', 'for', 'in', 'of', 'a', 'an', 'the', 'to', 'by', 'from', 'at', 'on', 'only', 'new', 'used', 'refurbished', 'pack'
]);

const ACCESSORY_WORDS = new Set([
  'accessory', 'accessories', 'battery', 'batteries', 'charger', 'chargers',
  'case', 'cases', 'cable', 'cables', 'part', 'parts', 'filter', 'filters',
  'stand', 'stands', 'attachment', 'attachments', 'mount', 'mounts',
  'brush', 'brushes', 'tool', 'tools', 'bag', 'bags', 'pouch', 'pouches',
  'refill', 'refills', 'replacement', 'replacements', 'strap', 'straps',
  'earpad', 'earpads', 'cushion', 'cushions', 'cover', 'covers', 'kit', 'kits'
]);

export async function probeAndUpdateProfile(
  slug: string,
  draft: Record<string, unknown>,
): Promise<ProbeAndUpdateResult> {
  const result: ProbeAndUpdateResult = {
    probed: false,
    reports: [],
    demotions: 0,
    followUpCommit: false,
  };

  try {
    // 1. Run the demote / gate pass
    const gated = await gateUniversalAiUrls(draft);
    result.probed = true;
    result.reports = gated.reports;
    result.demotions = gated.reports.filter((r) => !r.ok).length;

    // 2. Run the deterministic backfill pass
    const enrichedDraft = { ...gated.draft };
    const sources = Array.isArray(enrichedDraft.sources)
      ? [...(enrichedDraft.sources as Record<string, unknown>[])]
      : [];
    const displayName = typeof enrichedDraft.display_name === 'string' ? enrichedDraft.display_name : '';
    const displayNameClean = displayName.toLowerCase();
    const displayNameTokens = cleanTokens(displayName).filter((t) => !STOP_WORDS.has(t));

    // Parse spec_filters to get allowed conditions
    const specFilters = Array.isArray(enrichedDraft.spec_filters)
      ? (enrichedDraft.spec_filters as Record<string, unknown>[])
      : [];
    const conditionFilter = specFilters.find((f) => f && f.rule === 'condition_in');
    const allowedConditions = conditionFilter && Array.isArray(conditionFilter.values)
      ? new Set<string>(conditionFilter.values.map(String))
      : null;

    let backfilledAny = false;

    for (const host of FORCE_DETAIL_BACKUP_HOSTS) {
      // Find active universal_ai_search sources for this host
      const hostSources = sources.filter(
        (s) =>
          s &&
          s.id === 'universal_ai_search' &&
          typeof s.url === 'string' &&
          hostOf(s.url) === host
      );

      const hasSearch = hostSources.some((s) => s.page_type !== 'detail');
      const hasDetail = hostSources.some((s) => s.page_type === 'detail');

      // Trigger backfill if it has a search URL but no direct product-detail URL
      if (hasSearch && !hasDetail) {
        console.log(`[probe-and-update] Host ${host} triggered for detail backfill (has search, no detail)`);

        // Gather all JSON-LD candidate listings from successful search reports on this host
        const candidates: JsonLdListing[] = [];
        for (const s of hostSources) {
          if (s.page_type === 'detail') continue;
          const report = gated.reports.find((r) => r.url === s.url);
          if (report && report.ok && Array.isArray(report.listings)) {
            candidates.push(...report.listings);
          }
        }

        if (candidates.length === 0) {
          console.log(`[probe-and-update] No JSON-LD listings found for ${host} search URL(s)`);
          continue;
        }

        console.log(`[probe-and-update] Found ${candidates.length} raw JSON-LD candidates for ${host}`);

        // Filter candidates by title similarity, accessory exclusion, and condition
        const matchedCandidates = candidates.filter((c) => {
          if (!c.title || !c.url || typeof c.priceUsd !== 'number') return false;

          // Title similarity check: 60% match ratio of non-stop-word display name tokens
          const titleTokens = new Set(cleanTokens(c.title));
          if (displayNameTokens.length > 0) {
            const matchCount = displayNameTokens.filter((t) => titleTokens.has(t)).length;
            const matchRatio = matchCount / displayNameTokens.length;
            if (matchRatio < 0.6) {
              return false;
            }
          }

          // Accessory check: reject if title contains an accessory word not in display_name
          const titleTokensArr = cleanTokens(c.title);
          for (const token of titleTokensArr) {
            if (ACCESSORY_WORDS.has(token) && !displayNameClean.includes(token)) {
              return false;
            }
          }

          // Condition check
          if (allowedConditions) {
            const cond = c.condition ? String(c.condition).toLowerCase() : 'new';
            if (!allowedConditions.has(cond)) {
              return false;
            }
          }

          return true;
        });

        if (matchedCandidates.length === 0) {
          console.log(`[probe-and-update] 0 of ${candidates.length} candidates matched title / condition / accessory checks for ${host}`);
          continue;
        }

        console.log(`[probe-and-update] ${matchedCandidates.length} candidates passed initial checks for ${host}`);

        // Price cohort filter: find median, reject below 50% median, find basePrice, keep up to +30%
        const prices = matchedCandidates.map((c) => c.priceUsd);
        const medianPrice = getMedian(prices);
        
        const priceCleanedCands = matchedCandidates.filter((c) => c.priceUsd >= medianPrice * 0.5);
        if (priceCleanedCands.length === 0) continue;

        const basePrice = Math.min(...priceCleanedCands.map((c) => c.priceUsd));
        const finalCandidates = priceCleanedCands.filter((c) => c.priceUsd <= basePrice * 1.3);

        // Deduplicate by canonical URL
        const seenUrls = new Set<string>();
        const uniqueCandidates: JsonLdListing[] = [];
        for (const c of finalCandidates) {
          const canonical = canonicalUrl(c.url);
          if (!seenUrls.has(canonical)) {
            seenUrls.add(canonical);
            uniqueCandidates.push(c);
          }
        }

        console.log(`[probe-and-update] ${uniqueCandidates.length} unique candidates after price-band filters for ${host}`);

        // Keep at most 3 detail URLs to probe/append
        const keptCandidates = uniqueCandidates.slice(0, 3);

        // Find search source's alterlab options
        const searchSource = hostSources.find((s) => s.page_type !== 'detail');
        const extra = (searchSource?.extra && typeof searchSource.extra === 'object')
          ? (searchSource.extra as Record<string, unknown>)
          : null;
        const alterlabOptions = (extra && extra.alterlab_options && typeof extra.alterlab_options === 'object')
          ? (extra.alterlab_options as AlterlabOptions)
          : undefined;

        // Probe each kept detail URL and append if extractable
        let addedCount = 0;
        for (const candidate of keptCandidates) {
          if (addedCount >= 3) break;
          console.log(`[probe-and-update] Probing backfill candidate detail URL: ${candidate.url}`);
          try {
            const probeRes = await probeUrl(candidate.url, alterlabOptions, 'detail');
            if (probeRes.ok && probeRes.detailExtractable) {
              console.log(`[probe-and-update] SUCCESS: ${candidate.url} is detail-extractable. Appending source.`);
              sources.push({
                id: 'universal_ai_search',
                url: candidate.url,
                page_type: 'detail',
                extra: {
                  ...(alterlabOptions ? { alterlab_options: alterlabOptions } : {}),
                  page_type: 'detail',
                },
              });
              addedCount++;
              backfilledAny = true;
            } else {
              console.log(`[probe-and-update] FAILED: ${candidate.url} detail probe did not verify: ${probeRes.reason || 'not detailExtractable'}`);
            }
          } catch (probeErr) {
            console.error(`[probe-and-update] Error probing backfill candidate ${candidate.url}:`, probeErr);
          }
        }
      }
    }

    if (backfilledAny) {
      enrichedDraft.sources = sources;
    }

    // 3. Commit follow-up if we had demotions OR backfilled detail URLs
    if (result.demotions === 0 && !backfilledAny) {
      console.log(`[probe-and-update] ${slug}: all ${gated.reports.length} URL(s) passed and no backfill made — no follow-up commit needed`);
      return result;
    }

    if (result.demotions > 0) {
      console.log(
        `[probe-and-update] ${slug}: ${result.demotions}/${gated.reports.length} URL(s) demoted — committing follow-up`,
      );
      for (const r of gated.reports) {
        if (!r.ok) {
          console.log(`  ✗ ${r.url}: ${r.reason}`);
        }
      }
    }
    if (backfilledAny) {
      console.log(`[probe-and-update] ${slug}: backfilled detail URL(s) — committing follow-up`);
    }

    const updatedYaml = renderProfileYaml(enrichedDraft);
    await commitNewProfile(slug, updatedYaml);
    result.followUpCommit = true;

    console.log(`[probe-and-update] ${slug}: follow-up commit succeeded`);
  } catch (err) {
    // Best-effort — log and move on.
    console.error(
      `[probe-and-update] ${slug}: background probe failed:`,
      err instanceof Error ? err.message : String(err),
    );
  }

  return result;
}
