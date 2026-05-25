// ADR-079 (Phase 27 reinforcement): save-time guard for the gap the original
// ADR-079 implementation couldn't cover.
//
// Original ADR-079: the save-time gate (`gate-universal-ai.ts`) treats a probe
// as advisory for detail-preferred hosts — a weak probe on B&H / Best Buy /
// Target / Amazon (etc.) keeps the URL in `sources` with an advisory note
// rather than demoting it to `sources_pending`. Stronger than a single probe
// because the production runtime ladder + circuit breaker (ADR-078) own retry.
//
// Phase 26 / stress26-mx3s exposed the hole: the onboarder LLM can drop a
// detail-preferred URL **entirely** before the draft ever reaches the save
// gate — emitting a URL-less placeholder like:
//
//   sources_pending:
//     - id: universal_ai_search
//       note: B&H Photo detail URL failed extraction on this probe; will
//             retry search-style URL on next run
//
// The gate has no URL to protect, so the ADR-079 protection is bypassed and
// the vendor silently disappears.
//
// This check catches that shape deterministically. ANY URL-less
// `universal_ai_search` entry in `sources_pending` is a violation — the prompt
// (Phase 27 reinforcement) forbids that pattern unconditionally. Most often
// the dropped URL is a detail-preferred host (the LLM is most likely to bail
// when a hard-domain probe fails). The warning surfaces a soft warning so the
// user can fix-and-resave; the save still proceeds (same pattern as the other
// onboarder guards).
//
// Kept IMPORT-FREE on purpose — callers pass the registry host sets in — so
// this predicate can be unit-tested directly under `node --test`
// (`scripts/check-onboard-guards.test.mjs`), without the Next `@/` alias.

export interface DetailPresenceWarning {
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

// Detail-preferred host name fragments found in placeholder notes when the LLM
// dropped a URL. Built from the registry host strings (alphabetical hosts +
// common informal aliases) so a `bhphotovideo.com` placeholder still matches
// when the LLM writes "B&H Photo". Keep additions to genuinely unambiguous
// brand fragments so we don't false-match on incidental note text.
const HOST_ALIASES: Record<string, ReadonlyArray<string>> = {
  'amazon.com': ['amazon'],
  'backmarket.com': ['backmarket', 'back market'],
  'bestbuy.com': ['bestbuy', 'best buy'],
  'bhphotovideo.com': ['bhphotovideo', 'b&h', 'b & h', 'b and h'],
  'newegg.com': ['newegg'],
  'target.com': ['target'],
  'walmart.com': ['walmart'],
  'williams-sonoma.com': ['williams-sonoma', 'williams sonoma'],
};

// Best-effort: detect which detail-preferred host the note text is about.
// Returns null when no host fragment matches; the caller still warns generically
// in that case (the URL-less placeholder is the smell, not the host name).
function inferHostFromNote(
  note: string,
  detailHosts: ReadonlySet<string>,
): string | null {
  const low = note.toLowerCase();
  for (const host of detailHosts) {
    const fragments = HOST_ALIASES[host] ?? [host.replace(/\.com$/, '')];
    for (const frag of fragments) {
      if (low.includes(frag)) return host;
    }
  }
  return null;
}

export function checkDetailPreferencePresence(
  draft: Record<string, unknown>,
  forceDetailHosts: ReadonlySet<string>,
  preferDetailHosts: ReadonlySet<string>,
): DetailPresenceWarning[] {
  const sourcesPending = Array.isArray(draft.sources_pending)
    ? (draft.sources_pending as unknown[])
    : [];
  if (sourcesPending.length === 0) return [];

  // Hosts already protected by a URL-bearing source in `sources` — if the LLM
  // also emitted a URL-bearing source for the vendor, the placeholder is
  // redundant chatter, not a regression. (Don't double-warn.)
  const sources = Array.isArray(draft.sources) ? (draft.sources as unknown[]) : [];
  const presentHosts = new Set<string>();
  for (const s of sources) {
    if (!isObject(s) || s.id !== 'universal_ai_search') continue;
    const url = typeof s.url === 'string' ? s.url.trim() : '';
    if (!url) continue;
    const host = hostOf(url);
    if (host) presentHosts.add(host);
  }

  const detailHosts = new Set<string>([
    ...forceDetailHosts,
    ...preferDetailHosts,
  ]);

  const warnings: DetailPresenceWarning[] = [];
  for (const p of sourcesPending) {
    if (!isObject(p) || p.id !== 'universal_ai_search') continue;
    const url = typeof p.url === 'string' ? p.url.trim() : '';
    if (url) continue; // URL-bearing pending entries don't violate the rule.
    const note = typeof p.note === 'string' ? p.note : '';

    const inferredHost = inferHostFromNote(note, detailHosts);
    if (inferredHost && presentHosts.has(inferredHost)) continue;

    const noteExcerpt = note ? ` (note: ${JSON.stringify(note.slice(0, 140))})` : '';
    if (inferredHost) {
      warnings.push({
        message:
          `URL-less placeholder in sources_pending for ${inferredHost} — a ` +
          `detail-preferred vendor whose URL was dropped instead of kept in ` +
          `'sources' with a probe note. ADR-079 (Phase 27 reinforcement) ` +
          `requires keeping detail-preferred URLs in 'sources' with ` +
          `extra.probe_note; the runtime escalation ladder (ADR-078) handles ` +
          `retry better than a single probe. Open Edit Profile and restore ` +
          `the ${inferredHost} detail URL${noteExcerpt}.`,
      });
    } else {
      warnings.push({
        message:
          `A URL-less universal_ai_search entry appears in sources_pending — ` +
          `that pattern is the bypass the Phase 27 reinforcement of ADR-079 ` +
          `forbids. If the placeholder represents a detail-preferred vendor ` +
          `(B&H, Best Buy, Target, Amazon, …), open Edit Profile and restore ` +
          `its URL under 'sources' with extra.probe_note. The runtime owns ` +
          `retry; a single weak probe is not evidence the source is dead${noteExcerpt}.`,
      });
    }
  }
  return warnings;
}
