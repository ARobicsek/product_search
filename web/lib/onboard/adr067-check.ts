// Pure (no `import 'server-only'`, no `@/...` alias) so the offline
// `check-onboard-guards.test.mjs` suite can import it directly under raw node.
// Mirrors the convention of the other pure check modules
// (title-excludes-check, match-aliases-check, detail-preference-presence):
// the registry host set is passed in by the caller, NOT imported here. That
// keeps this file import-free and side-effect-free under raw node so tests
// don't need extension-resolution or path-alias shimming.

// ADR-067 / ADR-068 / ADR-111: save-time guardrail. For vendors flagged
// `force_detail_backup: true` in the vendor quirks registry, a single-SKU
// product MUST carry BOTH a search-style URL and a `page_type: "detail"`
// URL (search results on these big retailers are non-deterministic; the
// detail URL is the deterministic fallback). The onboarder prompt already
// instructs this, but the LLM has been observed to add only one — so this
// deterministic check catches the drift the prompt can't guarantee.
//
// ADR-111 (2026-05-28): this is now a HARD save-time error, not a soft
// warning. Live DJI-Neo-2 onboard left amazon/target/walmart with only
// search URLs despite the prompt saying "just do it"; the soft warning was
// surfaced post-save and the user then had to re-prompt the LLM and
// re-save. Making this a 422 error forces the LLM (via the validate_profile
// tool) and the user (via the save UI) to address it before save can
// complete, eliminating the manual round-trip.

// Kept as `Adr067Warning` (not renamed) for callers; semantically these are
// now hard errors (see ADR-111). validation.ts routes them to `errors`.
//
// `message` is the technical, LLM-facing text (ADR refs + the exact fix recipe
// the validate_profile tool feeds back to the model). `userMessage` is the
// plain-English version shown in the save UI / probe modal — no jargon, with a
// concrete "what to do next" (ADR-123).
export interface Adr067Warning {
  host: string;
  message: string;
  userMessage: string;
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

export function checkForceDetailBackup(
  draft: Record<string, unknown>,
  forceDetailBackupHosts: ReadonlySet<string>,
): Adr067Warning[] {
  const sources = Array.isArray(draft.sources) ? (draft.sources as unknown[]) : [];

  // Per host: did we see a search-style URL? a detail URL?
  const seen = new Map<string, { search: boolean; detail: boolean }>();

  for (const s of sources) {
    if (!isObject(s) || s.id !== 'universal_ai_search') continue;
    const url = typeof s.url === 'string' ? s.url.trim() : '';
    if (!url) continue;
    const host = hostOf(url);
    if (!host || !forceDetailBackupHosts.has(host)) continue;

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
          `non-deterministic, so save is BLOCKED until a direct product-detail URL ` +
          `is added (a second universal_ai_search source for this host with ` +
          `page_type: "detail"). Use web_search or a real search result to find the ` +
          `detail URL — do NOT guess a slug pattern — then probe it with ` +
          `page_type: "detail" and add it if detailExtractable is true. ` +
          `(ADR-067 / ADR-111.) ` +
          `If this product is genuinely multi-variant or this vendor rotates URLs, ` +
          `tell the user explicitly and drop this vendor from sources instead of ` +
          `saving with only the search URL.`,
        userMessage:
          `${host}: we have a search page but not a link to this exact product. ` +
          `Search results on big retailers shuffle around constantly, so we need ` +
          `the product's own page to track its price reliably. ` +
          `What to do: ask the assistant to find ${host}'s product page for this ` +
          `item (or paste the link yourself). If ${host} doesn't actually sell ` +
          `this exact product, ask the assistant to drop it.`,
      });
    } else if (detail && !search) {
      warnings.push({
        host,
        message:
          `${host}: only a detail URL is configured. Save is BLOCKED until a ` +
          `search-style URL is added (a second universal_ai_search source for ` +
          `this host) so newly-listed competing offers are still discovered, not ` +
          `just the one product page. (ADR-067 / ADR-111.)`,
        userMessage:
          `${host}: we have the direct product page but no search page. Adding a ` +
          `search page lets us also spot newly-listed offers for this item, not ` +
          `just this one listing. ` +
          `What to do: ask the assistant to add an ${host} search link for this product.`,
      });
    }
  }
  return warnings;
}
