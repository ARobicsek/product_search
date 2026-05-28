import { parseAndValidateProfileYaml, ProfileValidationError } from '@/lib/onboard/schema';
import { renderProfileYaml } from '@/lib/onboard/render-yaml';
import { checkForceDetailBackup } from '@/lib/onboard/adr067-check';
import { checkConditionDrift } from '@/lib/onboard/condition-drift-check';
import { checkTitleExcludes } from '@/lib/onboard/title-excludes-check';
import { checkDetailPreferencePresence } from '@/lib/onboard/detail-preference-presence';
import {
  FORCE_DETAIL_BACKUP_HOSTS,
  PREFER_DETAIL_HOSTS,
} from '@/lib/onboard/vendor-quirks-data';
import { checkMatchAliases } from '@/lib/onboard/match-aliases-check';
import { checkMatchAliasesAgainstHallucinatedSkus } from '@/lib/onboard/alias-hallucination-check';

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
  yamlText?: string;
  slug?: string;
}

export interface ValidationOptions {
  /**
   * ADR-115: when the user clicks "Save and proceed anyway" after the
   * save-time probe pass couldn't find a detail URL, bypass the ADR-111
   * force_detail_backup gate. The violation is downgraded to a warning so
   * the save can commit; the post-save background backfill will keep trying.
   */
  bypassForceDetailBackup?: boolean;
}

export function validateProfileDraft(
  draft: Record<string, unknown>,
  state: Record<string, unknown> | null = null,
  originalSlug: string | null = null,
  options: ValidationOptions = {}
): ValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  // 1. Guardrail checks.
  //
  // ADR-111 (2026-05-28): `checkForceDetailBackup` was demoted to `warnings`
  // historically (ADR-067 was advisory). It is now a HARD error: live DJI-Neo-2
  // onboard saved with amazon/target/walmart missing detail URLs, and the soft
  // warning required a manual re-prompt + re-save round-trip. Routing to
  // `errors` forces the `validate_profile` tool to return ok:false and the
  // save endpoint to 422 until the LLM/user fixes it.
  //
  // ADR-115 (2026-05-28): when the save-time probe + backfill couldn't land a
  // detail URL despite trying, the user can explicitly bypass this gate.
  // The violations become warnings so the user sees them in the save success
  // panel, but the save commits.
  const forceDetailViolations = checkForceDetailBackup(draft, FORCE_DETAIL_BACKUP_HOSTS).map((w) => w.message);
  if (options.bypassForceDetailBackup) {
    warnings.push(...forceDetailViolations);
  } else {
    errors.push(...forceDetailViolations);
  }
  warnings.push(...checkConditionDrift(state, draft).map(w => w.message));
  warnings.push(...checkTitleExcludes(draft).map(w => w.message));
  warnings.push(...checkDetailPreferencePresence(draft, FORCE_DETAIL_BACKUP_HOSTS, PREFER_DETAIL_HOSTS).map(w => w.message));
  warnings.push(...checkMatchAliases(draft).map(w => w.message));

  // ADR-116: a SKU/ASIN copied out of a source URL into match_aliases is a
  // hard error — at runtime it would let the carry-gate pass unrelated
  // listings. Hard-gate (like ADR-111) so the save 422s and ADR-113
  // auto-forwards the error to the LLM to fix.
  errors.push(...checkMatchAliasesAgainstHallucinatedSkus(draft).map(w => w.message));

  // 2. Render YAML
  let yamlText: string;
  try {
    yamlText = renderProfileYaml(draft);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'render-yaml failed';
    errors.push(`failed to render YAML from draft: ${msg}`);
    return { ok: false, errors, warnings };
  }

  const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
  if (originalSlug && SLUG_RE.test(originalSlug)) {
    yamlText = yamlText.replace(/^\s*slug\s*:\s*.*$/m, `slug: "${originalSlug}"`);
  }

  // 3. Schema validation
  let parsed;
  try {
    parsed = parseAndValidateProfileYaml(yamlText);
  } catch (err) {
    if (err instanceof ProfileValidationError) {
      errors.push(`profile failed schema validation`, ...err.errors);
    } else {
      const msg = err instanceof Error ? err.message : 'profile validation failed';
      errors.push(msg);
    }
    return { ok: false, errors, warnings, yamlText };
  }

  const slug = parsed.slug;
  if (!SLUG_RE.test(slug)) {
    errors.push(`slug ${JSON.stringify(slug)} fails ${SLUG_RE.source}`);
    return { ok: false, errors, warnings, yamlText };
  }

  return { ok: errors.length === 0, errors, warnings, yamlText, slug };
}
