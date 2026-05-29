import 'server-only';

// ADR-074 followup #1: save-time guardrail for dropped condition requirements.
//
// The onboarder prompt now instructs the LLM to translate a stated hard
// condition requirement ("new only", "no used / refurbished / open-box")
// into a `condition_in` spec_filter. But a prompt can't guarantee it — the
// 2026-05-21 prod onboard captured "new only" in chat yet emitted only
// `spec_filters: [in_stock]`, so used eBay listings dominated the report.
//
// This deterministic check compares the chat <state> ledger's
// `filters_summary` (where confirmed hard filters are recorded in prose)
// against the draft's actual `spec_filters`. If the ledger says the user
// asked for a condition constraint but no `condition_in` (and no covering
// `title_excludes`) made it into the draft, we surface a SOFT warning at
// save — same mechanism as the ADR-067 force_detail_backup warnings. The
// save still proceeds; the user can fix-and-resave or knowingly accept.

export interface ConditionDriftWarning {
  message: string;
  userMessage: string;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

// Phrases in the prose filter ledger that signal a hard condition
// requirement. Matched case-insensitively against each filters_summary line.
const CONDITION_PHRASE_RE =
  /\b(new[\s-]*only|only\s+new|brand[\s-]*new|must\s+be\s+new|new\s+condition|no\s+used|not?\s+used|no\s+refurb|no\s+open[\s-]*box|no\s+renewed|no\s+pre[\s-]*owned|condition\s*[:=]\s*new)\b/i;

// Terms in a title_excludes value that would cover a "no used/refurbished"
// intent — if the LLM chose title_excludes instead of condition_in, that's
// an acceptable (if weaker) translation, so don't warn.
const COVERING_EXCLUDE_RE = /\b(used|refurb|open[\s-]*box|renewed|pre[\s-]*owned)\b/i;

function draftHasConditionCoverage(draft: Record<string, unknown>): boolean {
  const filters = Array.isArray(draft.spec_filters) ? draft.spec_filters : [];
  for (const f of filters) {
    if (!isObject(f)) continue;
    if (f.rule === 'condition_in') {
      const values = Array.isArray(f.values) ? f.values : [];
      if (values.length > 0) return true;
    }
    if (f.rule === 'title_excludes') {
      const values = Array.isArray(f.values) ? f.values : [];
      if (values.some((v) => typeof v === 'string' && COVERING_EXCLUDE_RE.test(v))) {
        return true;
      }
    }
  }
  return false;
}

function statedConditionRequirements(state: Record<string, unknown>): string[] {
  const summary = Array.isArray(state.filters_summary) ? state.filters_summary : [];
  return summary.filter(
    (line): line is string => typeof line === 'string' && CONDITION_PHRASE_RE.test(line),
  );
}

export function checkConditionDrift(
  state: Record<string, unknown> | null | undefined,
  draft: Record<string, unknown>,
): ConditionDriftWarning[] {
  if (!isObject(state)) return [];
  const stated = statedConditionRequirements(state);
  if (stated.length === 0) return [];
  if (draftHasConditionCoverage(draft)) return [];

  const quoted = stated.map((s) => `"${s.trim()}"`).join('; ');
  return [
    {
      message:
        `A hard condition requirement was confirmed in chat (${quoted}) but the ` +
        `saved profile has no condition_in filter — used / refurbished listings ` +
        `will NOT be rejected and may dominate the report. Add a spec_filter ` +
        `{rule: "condition_in", values: ["new"]} (ADR-074), or re-confirm that ` +
        `any condition is acceptable.`,
      userMessage:
        `You asked for a specific condition (${quoted}) but the saved profile ` +
        `doesn't filter by condition — used or refurbished listings won't be ` +
        `rejected and could show up as the "cheapest" price. ` +
        `What to do: ask the assistant to add a "new only" condition filter, or ` +
        `confirm you're OK with any condition.`,
    },
  ];
}
