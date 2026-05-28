// ADR-116: match_aliases hallucination guard (save-time, hard error).
//
// Pure (no `import 'server-only'`, no `@/...` alias) so the offline
// `check-onboard-guards.test.mjs` suite can import it directly under raw node —
// same convention as adr067-check / match-aliases-check.
//
// The bug this closes (the DJI Neo 2 run, 2026-05-28): the onboarder built a
// detail source `amazon.com/.../dp/B0FJ1QH15P` (the wrong product) AND copied
// the ASIN `B0FJ1QH15P` straight into `match_aliases`. An ASIN lifted out of a
// URL is NOT a verified marketing alias — and worse, at runtime the carry-gate
// (ADR-099) PASSES any page whose text contains a match_alias, so a stray ASIN
// in match_aliases can silently green-light unrelated listings. The signature of
// this mistake is unmistakable: the SAME SKU token appears both in a source URL
// and in match_aliases. We hard-reject that, with an LLM-actionable message so
// ADR-113's auto-forward prompts the model to fix the draft.

interface AliasHallucinationWarning {
  message: string;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/**
 * Extract SKU/identifier tokens embedded in a vendor URL. Conservative — only
 * patterns long enough to be unambiguous (so we never mistake a slug word for a
 * SKU):
 *   - Amazon ASIN:      /dp/<10-char>, /gp/product/<10-char>, /gp/aw/d/<10-char>
 *   - B&H:              /<digits>-REG (or -USED / -GRY / -BB) product numbers
 * Returns tokens normalized to uppercase-alphanumeric.
 */
export function extractSkuTokens(url: string): string[] {
  const out: string[] = [];
  const push = (t: string | undefined) => {
    if (!t) return;
    const norm = t.toUpperCase().replace(/[^A-Z0-9]+/g, '');
    if (norm) out.push(norm);
  };

  // Amazon ASIN (10-char alphanumeric).
  const asin = url.match(/\/(?:dp|gp\/product|gp\/aw\/d)\/([A-Za-z0-9]{10})(?:[/?#]|$)/);
  push(asin?.[1]);

  // B&H product number (6+ digits) followed by a market suffix.
  const bh = url.match(/\/(\d{6,})-(?:REG|USED|GRY|BB)\b/i);
  push(bh?.[1]);

  return Array.from(new Set(out));
}

/**
 * ADR-116 guard: reject a save where a match_alias is a SKU/ASIN copied out of a
 * source URL. Returns hard-error messages (validation.ts routes them to
 * `errors`, so the save 422s until fixed).
 */
export function checkMatchAliasesAgainstHallucinatedSkus(
  draft: Record<string, unknown>,
): AliasHallucinationWarning[] {
  const aliasesRaw = Array.isArray(draft.match_aliases) ? draft.match_aliases : [];
  const aliases = aliasesRaw.filter((a): a is string => typeof a === 'string' && a.trim().length > 0);
  if (aliases.length === 0) return [];

  // Normalize each alias to uppercase-alphanumeric for substring comparison.
  const aliasNorms = aliases.map((a) => ({ raw: a, norm: a.toUpperCase().replace(/[^A-Z0-9]+/g, '') }));

  const sources = Array.isArray(draft.sources) ? (draft.sources as unknown[]) : [];
  const warnings: AliasHallucinationWarning[] = [];
  const reported = new Set<string>();

  for (const s of sources) {
    if (!isObject(s) || s.id !== 'universal_ai_search') continue;
    const url = typeof s.url === 'string' ? s.url.trim() : '';
    if (!url) continue;
    for (const token of extractSkuTokens(url)) {
      for (const { raw, norm } of aliasNorms) {
        if (norm.includes(token) && !reported.has(token)) {
          reported.add(token);
          warnings.push({
            message:
              `match_aliases entry ${JSON.stringify(raw)} contains the SKU/ASIN "${token}", ` +
              `which is taken straight from the source URL ${url}. A SKU copied out of a URL is ` +
              `NOT a verified product alias — at runtime the carry-gate will PASS any page whose ` +
              `text merely contains it, silently admitting unrelated listings (ADR-116). ` +
              `Remove "${token}" from match_aliases and derive aliases from the product's actual ` +
              `marketing name / model number instead. If you genuinely need this vendor as a ` +
              `detail source, keep the URL only after confirming the probed page's title is the ` +
              `requested product.`,
          });
        }
      }
    }
  }

  return warnings;
}
