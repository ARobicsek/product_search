import 'server-only';

import { probeUrl, type ProbeResult } from '@/lib/onboard/probe-url';

// Phase 15 task 5: server-side gate that runs at /api/onboard/save time.
// For each ``universal_ai_search`` source on the draft, we probe the URL
// (raw fetch + JSON-LD + product-anchor count). 0-candidate URLs are
// MOVED — not deleted — to ``sources_pending`` with a probe-failure note,
// so the saved profile.yaml never carries a vendor URL that yields nothing.
//
// Why move and not delete: the user might have intentionally added a URL
// that's currently bot-blocked but readable in the future, OR a URL that
// only a stronger fetcher (production AlterLab tier) can handle. Demoting
// preserves intent; the user can later promote it back with a one-line edit.
//
// Why not invoke this at chat time as a tool: the chat route is per-turn,
// and probing 5 URLs in one turn costs 5x the latency budget. Save-time is
// the natural moment because the user has finished iterating.

export interface ProbeReport {
  url: string;
  ok: boolean;
  jsonldCount: number;
  anchorCount: number;
  reason: string | null;
}

interface GateOutput {
  draft: Record<string, unknown>;
  reports: ProbeReport[];
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

export async function gateUniversalAiUrls(
  draft: Record<string, unknown>,
): Promise<GateOutput> {
  const sourcesIn = Array.isArray(draft.sources) ? (draft.sources as unknown[]) : [];
  const pendingIn = Array.isArray(draft.sources_pending)
    ? (draft.sources_pending as unknown[])
    : [];

  // Identify universal_ai_search entries in `sources` that need probing.
  // Non-universal_ai sources pass through untouched.
  type Pending = { source: Record<string, unknown>; url: string };
  const toProbe: Pending[] = [];
  const sourcesOut: unknown[] = [];

  for (const s of sourcesIn) {
    if (!isObject(s)) {
      sourcesOut.push(s);
      continue;
    }
    if (s.id !== 'universal_ai_search') {
      sourcesOut.push(s);
      continue;
    }
    const url = typeof s.url === 'string' ? s.url.trim() : '';
    if (!url) {
      // No URL → nothing to probe; let schema validation catch it later.
      sourcesOut.push(s);
      continue;
    }
    toProbe.push({ source: s, url });
  }

  // Probe in parallel; one slow vendor doesn't gate the rest.
  const results = await Promise.all(
    toProbe.map((p) => probeUrl(p.url).catch((err: unknown) => {
      // probeUrl already catches its own errors and returns ok=false; this
      // is a paranoia layer for unexpected exceptions.
      const msg = err instanceof Error ? err.message : String(err);
      return {
        ok: false,
        url: p.url,
        fetchStatus: null,
        bodyLength: 0,
        jsonldCount: 0,
        anchorCount: 0,
        reason: `unexpected probe error: ${msg}`,
      } satisfies ProbeResult;
    })),
  );

  const reports: ProbeReport[] = results.map((r) => ({
    url: r.url,
    ok: r.ok,
    jsonldCount: r.jsonldCount,
    anchorCount: r.anchorCount,
    reason: r.reason,
  }));

  // Bucket each probed source: passing → sources, failing → sources_pending.
  const newPending: unknown[] = [...pendingIn];
  for (let i = 0; i < toProbe.length; i++) {
    const { source } = toProbe[i];
    const result = results[i];
    if (result.ok) {
      sourcesOut.push(source);
      continue;
    }
    // Demote: keep the original source body intact so a later promotion
    // round-trips cleanly, but add a note explaining why it landed here.
    const reason = result.reason ?? 'probe returned 0 candidates';
    const demoted: Record<string, unknown> = {
      ...source,
      note: `probe returned 0 candidates: ${reason}`,
    };
    newPending.push(demoted);
  }

  return {
    draft: {
      ...draft,
      sources: sourcesOut,
      sources_pending: newPending,
    },
    reports,
  };
}
