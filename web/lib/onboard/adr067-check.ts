import 'server-only';

import { FORCE_DETAIL_BACKUP_HOSTS } from '@/lib/onboard/vendor-quirks-data';

// ADR-067 / ADR-068: save-time guardrail. For vendors flagged
// `force_detail_backup: true` in the vendor quirks registry, a single-SKU
// product should carry BOTH a search-style URL and a `page_type: "detail"`
// URL (search results on these big retailers are non-deterministic; the
// detail URL is the deterministic fallback). The onboarder prompt already
// instructs this, but the LLM has been observed to add only one — so this
// deterministic check catches the drift the prompt can't guarantee.
//
// This is a SOFT warning, not a hard block: legitimate edge cases exist
// (multi-variant detail pages that surface the wrong variant, slug-rotating
// stores). The save proceeds; the warnings are returned to the chat surface
// so the user can fix-and-resave or knowingly accept.

export interface Adr067Warning {
  host: string;
  message: string;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function hostOf(url: string): string | null {
  try {
    const h = new URL(url).host.toLowerCase();
    return h.startsWith('www.') ? h.slice(4) : h;
  } catch {
    return null;
  }
}

export function checkForceDetailBackup(draft: Record<string, unknown>): Adr067Warning[] {
  const sources = Array.isArray(draft.sources) ? (draft.sources as unknown[]) : [];

  // Per host: did we see a search-style URL? a detail URL?
  const seen = new Map<string, { search: boolean; detail: boolean }>();

  for (const s of sources) {
    if (!isObject(s) || s.id !== 'universal_ai_search') continue;
    const url = typeof s.url === 'string' ? s.url.trim() : '';
    if (!url) continue;
    const host = hostOf(url);
    if (!host || !FORCE_DETAIL_BACKUP_HOSTS.has(host)) continue;

    const isDetail = s.page_type === 'detail';
    const entry = seen.get(host) ?? { search: false, detail: false };
    if (isDetail) entry.detail = true;
    else entry.search = true;
    seen.set(host, entry);
  }

  const warnings: Adr067Warning[] = [];
  for (const [host, { search, detail }] of seen) {
    if (search && detail) continue;
    if (search && !detail) {
      warnings.push({
        host,
        message:
          `${host}: only a search URL is configured. ${host} search results are ` +
          `non-deterministic — add a direct product-detail URL (a second ` +
          `universal_ai_search source with page_type: "detail") so the product ` +
          `isn't missed on runs where the search hiccups (ADR-067). Skip only if ` +
          `the product is multi-variant or this vendor rotates URLs.`,
      });
    } else if (detail && !search) {
      warnings.push({
        host,
        message:
          `${host}: only a detail URL is configured. Add a search-style URL too ` +
          `(a second universal_ai_search source) so newly-listed competing offers ` +
          `are still discovered, not just the one product page (ADR-067).`,
      });
    }
  }
  return warnings;
}
