// ADR-080: save-time guardrail for fragile title_excludes values.
//
// title_excludes is a plain (case-insensitive) substring reject. The 2026-05-24
// eval caught the onboarder emitting values that silently zero recall:
//   - "MX Master 3" is a substring of the wanted "MX Master 3S" → rejects the
//     target itself;
//   - "bowl" rejected a real "KitchenAid … with Copper Bowl" mixer.
// The prompt now forbids both, but a prompt can't guarantee it. This pure check
// surfaces a SOFT warning at save (same mechanism as the ADR-067/074 warnings)
// when a title_excludes value is a substring of the product name. The save still
// proceeds — the user can fix-and-resave or knowingly accept.

export interface TitleExcludesWarning {
  message: string;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function norm(s: string): string {
  return s.toLowerCase().trim();
}

// Returns the product-name strings to test exclude values against: the
// display_name and the slug (de-hyphenated), both normalized.
function productNames(draft: Record<string, unknown>): string[] {
  const names: string[] = [];
  if (typeof draft.display_name === 'string' && draft.display_name.trim()) {
    names.push(norm(draft.display_name));
  }
  if (typeof draft.slug === 'string' && draft.slug.trim()) {
    names.push(norm(draft.slug.replace(/-/g, ' ')));
  }
  return names;
}

export function checkTitleExcludes(
  draft: Record<string, unknown>,
): TitleExcludesWarning[] {
  const filters = Array.isArray(draft.spec_filters) ? draft.spec_filters : [];
  const names = productNames(draft);
  if (names.length === 0) return [];

  const offenders: string[] = [];
  for (const f of filters) {
    if (!isObject(f) || f.rule !== 'title_excludes') continue;
    const values = Array.isArray(f.values) ? f.values : [];
    for (const v of values) {
      if (typeof v !== 'string' || !v.trim()) continue;
      const nv = norm(v);
      // Flag when the exclude value is a substring of the product name — it
      // would reject the target product itself (the "MX Master 3" ⊂ "MX Master
      // 3S" trap). Skip 1-char values (too noisy to be meaningful).
      if (nv.length >= 2 && names.some((name) => name.includes(nv))) {
        offenders.push(v.trim());
      }
    }
  }
  if (offenders.length === 0) return [];

  const quoted = [...new Set(offenders)].map((s) => `"${s}"`).join('; ');
  return [
    {
      message:
        `A title_excludes value (${quoted}) is a substring of the product name — ` +
        `it will reject the target product itself and silently zero recall. ` +
        `Remove it (let the relevance filter handle near-models/accessories), or ` +
        `narrow it to a token that does NOT appear in the product name (ADR-080).`,
    },
  ];
}
