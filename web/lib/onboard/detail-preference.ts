// ADR-079: which universal_ai_search sources the vendor registry says NEED a
// detail URL — so the save-time gate treats a probe as advisory (keeps the
// source) rather than demoting it on one unlucky weak fetch.
//
// Kept IMPORT-FREE on purpose: callers pass the registry host sets in. That lets
// this predicate be unit-tested directly under `node --test` (a module with any
// relative import isn't resolvable there without the Next `@/` alias / extension
// handling). gate-universal-ai.ts supplies the real sets from vendor-quirks-data.

function hostOf(url: string): string | null {
  try {
    const h = new URL(url).host.toLowerCase();
    return h.startsWith('www.') ? h.slice(4) : h;
  } catch {
    return null;
  }
}

// True when the registry marks this URL's vendor as detail-preferred (search
// tiles blind / single-SKU detail-only) OR the source itself is a detail URL.
// For these a transient probe failure must NOT demote the source — the runtime
// escalation ladder + circuit breaker (ADR-071/078) own retry.
export function isDetailPreferred(
  url: string,
  pageType: 'search' | 'detail' | undefined,
  forceDetailHosts: ReadonlySet<string>,
  preferDetailHosts: ReadonlySet<string>,
): boolean {
  if (pageType === 'detail') return true;
  const host = hostOf(url);
  if (host === null) return false;
  return forceDetailHosts.has(host) || preferDetailHosts.has(host);
}
