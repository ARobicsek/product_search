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
  // Technical, LLM-facing text (ADR refs + the exact fix recipe). Consumed by
  // the `validate_profile` tool so the model can self-correct. Unchanged.
  errors: string[];
  warnings: string[];
  // ADR-123: plain-English, user-facing versions kept in lockstep with
  // errors/warnings. The save UI + probe modal render THESE so the user never
  // sees internal jargon. Every errors[i]/warnings[i] has a matching
  // userErrors[i]/userWarnings[i] (falls back to the technical text when a
  // particular check predates this split).
  userErrors: string[];
  userWarnings: string[];
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
  // ADR-123: user-facing copies, kept positionally in lockstep with the two
  // arrays above via the push helpers below.
  const userErrors: string[] = [];
  const userWarnings: string[] = [];

  // Push a technical message + its plain-English twin together so the two
  // arrays never drift. When a source has no dedicated user message (schema /
  // slug / render errors), the technical text is reused as the fallback.
  const pushError = (message: string, userMessage?: string) => {
    errors.push(message);
    userErrors.push(userMessage ?? message);
  };
  const pushWarning = (message: string, userMessage?: string) => {
    warnings.push(message);
    userWarnings.push(userMessage ?? message);
  };

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
  const forceDetailViolations = checkForceDetailBackup(draft, FORCE_DETAIL_BACKUP_HOSTS);
  for (const w of forceDetailViolations) {
    if (options.bypassForceDetailBackup) pushWarning(w.message, w.userMessage);
    else pushError(w.message, w.userMessage);
  }
  for (const w of checkConditionDrift(state, draft)) pushWarning(w.message, w.userMessage);
  for (const w of checkTitleExcludes(draft)) pushWarning(w.message, w.userMessage);
  for (const w of checkDetailPreferencePresence(draft, FORCE_DETAIL_BACKUP_HOSTS, PREFER_DETAIL_HOSTS)) {
    pushWarning(w.message, w.userMessage);
  }
  for (const w of checkMatchAliases(draft)) pushWarning(w.message, w.userMessage);

  // ADR-116: a SKU/ASIN copied out of a source URL into match_aliases is a
  // hard error — at runtime it would let the carry-gate pass unrelated
  // listings. Hard-gate (like ADR-111) so the save 422s and ADR-113
  // auto-forwards the error to the LLM to fix.
  for (const w of checkMatchAliasesAgainstHallucinatedSkus(draft)) pushError(w.message, w.userMessage);

  // 2. Render YAML
  let yamlText: string;
  try {
    yamlText = renderProfileYaml(draft);
  } catch (err) {
    const msg = err instanceof Error ? err.message : 'render-yaml failed';
    pushError(
      `failed to render YAML from draft: ${msg}`,
      `Something went wrong turning your answers into a profile. This is usually a ` +
        `temporary glitch — ask the assistant to try saving again.`,
    );
    return { ok: false, errors, warnings, userErrors, userWarnings };
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
      const friendly =
        `The profile didn't match the required format. Ask the assistant to ` +
        `review and fix it, then save again.`;
      pushError(`profile failed schema validation`, friendly);
      for (const e of err.errors) pushError(e, friendly);
    } else {
      const msg = err instanceof Error ? err.message : 'profile validation failed';
      pushError(
        msg,
        `The profile couldn't be validated. Ask the assistant to review and fix ` +
          `it, then save again.`,
      );
    }
    return { ok: false, errors, warnings, userErrors, userWarnings, yamlText };
  }

  const slug = parsed.slug;
  if (!SLUG_RE.test(slug)) {
    pushError(
      `slug ${JSON.stringify(slug)} fails ${SLUG_RE.source}`,
      `The product's short name (slug) has invalid characters. Ask the assistant ` +
        `to pick a simple lowercase-and-hyphens name.`,
    );
    return { ok: false, errors, warnings, userErrors, userWarnings, yamlText };
  }

  return { ok: errors.length === 0, errors, warnings, userErrors, userWarnings, yamlText, slug };
}
