// ADR-116: detail-URL relevance gate.
//
// Pure (no `import 'server-only'`, no `@/...` alias, no Anthropic SDK) so the
// offline `check-onboard-guards.test.mjs` suite can import it directly under
// raw node — mirrors the convention of the other pure check modules
// (adr067-check, match-aliases-check, title-excludes-check).
//
// Why deterministic and not "ask Haiku if the page matches": the architectural
// thesis (docs/ARCHITECTURE.md, CLAUDE.md hard rules) is that the LLM is trusted
// only to SYNTHESIZE pre-verified data, never to VERIFY. The detail page's title
// is pulled verbatim from the page (JSON-LD `name` or the extractor's verbatim
// title — never fabricated, ADR-001); the relevance JUDGMENT is then made here,
// deterministically, by token overlap against the target product name.
//
// The bug this closes (the DJI Neo 2 run, 2026-05-28): the save-time probe
// hard-coded a pass for any detail page that yielded an extractable price, so
// `amazon.com/.../dp/B0FJ1QH15P` — the DJI *Transmission Transceiver*, a totally
// different product — was baked into the profile as the Neo 2's detail source
// (and its ASIN polluted match_aliases). Its title shares only the BRAND token
// ("DJI") with the target; everything else is disjoint. This gate rejects it.

// Bias note: this gate sets `ok = false` (blocks the source) on a mismatch,
// which is the OPPOSITE bias from the general probe policy (which never demotes
// on a weak signal). That is intentional and scoped to detail relevance: a false
// ACCEPT silently bakes the wrong product into the profile (the P0 we are fixing
// and hard to notice), whereas a false REJECT is loud and recoverable (the LLM
// re-probes / web_searches, or the user clicks "Save and proceed anyway"). When
// in doubt, reject.

const STOP_WORDS = new Set([
  'the', 'and', 'for', 'with', 'new', 'set', 'kit', 'pack', 'pair',
  'lot', 'box', 'qty', 'pcs', 'item', 'items', 'product', 'products',
]);

/**
 * Distinctive tokens used to judge whether a detail page is the requested
 * product. PREFERS model-number-shaped tokens (a word with BOTH a letter and a
 * digit, e.g. `WH-1000XM5` → `wh1000xm5`); only when the name has no such token
 * does it fall back to generic ≥3-char words. Mirrors `distinctiveTokens` in
 * probe-url.ts (kept separate so this module stays import-free for raw node).
 */
export function familyRootTokens(name: string): string[] {
  const words = name.toLowerCase().split(/[\s,.()+]+/).filter(Boolean);
  const modelTokens = words
    .filter((w) => /[a-z]/.test(w) && /\d/.test(w))
    .map((w) => w.replace(/[^a-z0-9]+/g, ''))
    .filter((w) => w.length >= 4);
  if (modelTokens.length > 0) return Array.from(new Set(modelTokens));
  return Array.from(
    new Set(
      words
        .flatMap((w) => w.split(/[-/]/))
        .filter((t) => t.length >= 3 && !STOP_WORDS.has(t)),
    ),
  );
}

/**
 * Does a detail page's (verbatim) product title plausibly refer to the target
 * product? Token-overlap heuristic, deliberately lenient about VARIANT (a
 * sibling SKU that shares the family root passes — variant strictness is
 * ADR-117's job) but strict about IDENTITY (a different product that shares only
 * the brand fails).
 *
 * Returns:
 *   - `null`  when no judgment is possible (empty target name, no distinctive
 *             tokens, or empty title) — the caller leaves the probe ungated.
 *   - `true`  when the title carries enough of the target's distinctive tokens.
 *   - `false` when it does not (brand-only or fully-disjoint match).
 *
 * Rule: with a single distinctive token (typically a model number) that token
 * must appear; with two or more, at least two must appear. Requiring two guards
 * against a brand-only match (the Transceiver shares only "dji"), while
 * accepting a terse-but-correct title ("DJI Neo 2 Drone" → dji+neo).
 */
export function titleMatchesTarget(
  title: string | null | undefined,
  targetName: string | null | undefined,
): boolean | null {
  if (!targetName || !targetName.trim()) return null;
  if (!title || !title.trim()) return null;
  const tokens = familyRootTokens(targetName);
  if (tokens.length === 0) return null;
  const norm = title.toLowerCase().replace(/[^a-z0-9]+/g, '');
  const matched = tokens.filter((t) => norm.includes(t)).length;
  const needed = tokens.length <= 1 ? 1 : 2;
  return matched >= needed;
}
