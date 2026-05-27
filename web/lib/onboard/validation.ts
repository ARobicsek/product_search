import { parseAndValidateProfileYaml, ProfileValidationError } from '@/lib/onboard/schema';
import { renderProfileYaml } from '@/lib/onboard/render-yaml';
import { checkForceDetailBackup, type Adr067Warning } from '@/lib/onboard/adr067-check';
import { checkConditionDrift } from '@/lib/onboard/condition-drift-check';
import { checkTitleExcludes } from '@/lib/onboard/title-excludes-check';
import { checkDetailPreferencePresence } from '@/lib/onboard/detail-preference-presence';
import {
  FORCE_DETAIL_BACKUP_HOSTS,
  PREFER_DETAIL_HOSTS,
} from '@/lib/onboard/vendor-quirks-data';
import { checkMatchAliases } from '@/lib/onboard/match-aliases-check';

export interface ValidationResult {
  ok: boolean;
  errors: string[];
  warnings: string[];
  yamlText?: string;
  slug?: string;
}

export function validateProfileDraft(
  draft: Record<string, unknown>,
  state: Record<string, unknown> | null = null,
  originalSlug: string | null = null
): ValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];

  // 1. Guardrail checks
  warnings.push(...checkForceDetailBackup(draft).map(w => w.message));
  warnings.push(...checkConditionDrift(state, draft).map(w => w.message));
  warnings.push(...checkTitleExcludes(draft).map(w => w.message));
  warnings.push(...checkDetailPreferencePresence(draft, FORCE_DETAIL_BACKUP_HOSTS, PREFER_DETAIL_HOSTS).map(w => w.message));
  warnings.push(...checkMatchAliases(draft).map(w => w.message));

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
