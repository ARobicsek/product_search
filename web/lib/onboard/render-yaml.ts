// Server- and client-shared deterministic YAML renderer for the Phase 14
// structured-intent flow. The onboarder LLM emits a <draft>{...}</draft>
// block per turn that mirrors the profile schema 1:1; this module dumps it
// to YAML so the user (and the GitHub commit) sees a single source of truth.
//
// Why server-side: eliminates the "model dropped a closing brace in YAML"
// failure class. The model only has to emit valid JSON; YAML formatting is
// our problem.

import yaml from 'js-yaml';

// Top-level keys, in the order the onboard prompt documents them. js-yaml's
// default key order follows insertion order, so we re-build the object in
// canonical order before dumping. Unknown keys are appended at the end (the
// validator will reject them but they survive the round-trip for debugging).
const CANONICAL_KEY_ORDER: ReadonlyArray<string> = [
  'slug',
  'display_name',
  'description',
  'target',
  'spec_attrs',
  'spec_filters',
  'spec_flags',
  'sources',
  'sources_pending',
  'qvl_file',
  'brand_candidates',
  'synthesis_hints',
  'report_columns',
  'schedule',
];

function canonicalize(input: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of CANONICAL_KEY_ORDER) {
    if (k in input) out[k] = input[k];
  }
  for (const k of Object.keys(input)) {
    if (!(k in out)) out[k] = input[k];
  }
  return out;
}

export function renderProfileYaml(intent: Record<string, unknown>): string {
  const ordered = canonicalize(intent);
  // js-yaml block style with reasonable defaults. lineWidth: -1 disables
  // forced line wrapping inside long strings (descriptions, notes).
  return yaml.dump(ordered, {
    indent: 2,
    lineWidth: -1,
    noRefs: true,
    sortKeys: false,
    quotingType: '"',
    forceQuotes: false,
  });
}
