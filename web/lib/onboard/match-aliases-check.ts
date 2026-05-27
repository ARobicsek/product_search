import 'server-only';

/**
 * Derive a recall-safe family-core token from a product display_name.
 * 
 * Mirrors `_model_family_token` from worker/src/product_search/adapters/universal_ai.py.
 * Extracts the longest whitespace word containing BOTH a letter and a digit,
 * reduces it to the first hyphen/slash segment that still contains a digit,
 * and normalizes it to lowercase-alphanumeric.
 * 
 * Returns null when no confident model token exists (normalized core < 5 chars).
 */
export function modelFamilyToken(displayName: string): string | null {
  if (!displayName) return null;
  
  const words = displayName.trim().split(/\s+/);
  const candidates = words.filter(w => /[a-zA-Z]/.test(w) && /\d/.test(w));
  
  if (candidates.length === 0) return null;
  
  let modelWord = candidates[0];
  for (const c of candidates) {
    if (c.length > modelWord.length) {
      modelWord = c;
    }
  }
  
  const segments = modelWord.split(/[-/]/);
  let core = modelWord;
  for (const s of segments) {
    if (/\d/.test(s)) {
      core = s;
      break;
    }
  }
  
  const norm = core.toLowerCase().replace(/[^a-z0-9]+/g, '');
  return norm.length >= 5 ? norm : null;
}

/**
 * ADR-101 guard: Enforce match_aliases seeding to protect the ADR-099 carry-gate.
 * 
 * - If match_aliases is populated, returns no warnings.
 * - If missing/empty and NO confident model token exists, throws an Error to hard-reject the save.
 * - If missing/empty but a confident model token DOES exist, returns a soft warning.
 */
export function checkMatchAliases(draft: Record<string, unknown>): { message: string }[] {
  let hasAlias = false;
  const aliasesRaw = draft.match_aliases;
  
  if (Array.isArray(aliasesRaw)) {
    for (const a of aliasesRaw) {
      if (typeof a === 'string' && a.trim().length > 0) {
        hasAlias = true;
        break;
      }
    }
  }

  if (!hasAlias) {
    const displayName = typeof draft.display_name === 'string' ? draft.display_name : '';
    const token = modelFamilyToken(displayName);
    
    if (!token) {
      throw new Error(
        "match_aliases is missing/empty AND no confident model token could be derived from display_name. " +
        "You MUST provide match_aliases (marketing names or SKU variations) for the runtime carry-gate."
      );
    } else {
      return [{
        message: `match_aliases is empty. The runtime will rely solely on the derived model token '${token}' for the carry-gate. Please seed match_aliases to ensure reliable vendor matching.`
      }];
    }
  }
  
  return [];
}
