const fs = require('fs');
const path = require('path');
const yaml = require('js-yaml');

// Generates two artifacts from the source-of-truth files (ADR-068):
//   1. web/lib/onboard/promptText.ts   — the onboarder system prompt, with the
//      <!-- VENDOR_QUIRKS_BEGIN/END --> block filled in from the vendor
//      quirks registry so prompt + adapter never drift apart.
//   2. web/lib/onboard/vendor-quirks-data.ts — structured registry data the
//      save-time gate uses for ADR-067 force_detail_backup enforcement and
//      the probe-url AlterLab-known-good allowlist.
//
// Sources:
//   - worker/src/product_search/onboarding/prompts/onboard_v1.txt
//   - worker/src/product_search/vendor_quirks.yaml

const promptSrc = path.join(
  __dirname,
  '../../worker/src/product_search/onboarding/prompts/onboard_v1.txt',
);
const promptDest = path.join(__dirname, '../lib/onboard/promptText.ts');
const quirksSrc = path.join(
  __dirname,
  '../../worker/src/product_search/vendor_quirks.yaml',
);
const quirksDest = path.join(__dirname, '../lib/onboard/vendor-quirks-data.ts');

const VENDOR_QUIRKS_BEGIN = '<!-- VENDOR_QUIRKS_BEGIN -->';
const VENDOR_QUIRKS_END = '<!-- VENDOR_QUIRKS_END -->';

function normalizeHost(host) {
  let h = (host || '').toLowerCase().trim();
  if (h.startsWith('www.')) h = h.slice(4);
  return h;
}

// Render the registry to the human-readable prose the LLM consumes. Order is
// alphabetical by host for deterministic output (stable git diffs).
function renderQuirksProse(registry) {
  const hosts = Object.keys(registry).sort();
  const lines = [];
  for (const host of hosts) {
    const entry = registry[host] || {};
    const parts = [];

    const known = entry.known_failure;
    if (known && typeof known === 'object') {
      const summary = String(known.summary || '').replace(/\s+/g, ' ').trim();
      const action = String(known.onboarder_action || '').replace(/\s+/g, ' ').trim();
      parts.push(
        `KNOWN FAILURE (${known.severity || 'blocker'}) — ${summary}` +
          (action ? ` ACTION: ${action}` : ''),
      );
    }

    const opts = entry.default_alterlab_options;
    if (opts && typeof opts === 'object' && Object.keys(opts).length > 0) {
      const rendered = Object.entries(opts)
        .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
        .join(', ');
      parts.push(`default alterlab_options \`{ ${rendered} }\` (auto-merged by the adapter)`);
    }

    if (entry.force_detail_backup === true) {
      parts.push(
        'ADR-067: single-SKU products on this vendor need BOTH a search URL ' +
          'AND a `page_type: "detail"` URL (two separate `universal_ai_search` sources)',
      );
    }

    if (entry.prefer_page_type) {
      parts.push(`PREFER \`page_type: "${entry.prefer_page_type}"\` URLs over search URLs`);
    }

    if (Array.isArray(entry.url_transforms) && entry.url_transforms.length > 0) {
      parts.push(
        'the adapter auto-applies URL transform(s) for this host before fetch ' +
          '(no action needed from you)',
      );
    }

    if (entry.notes) {
      parts.push(`Notes: ${String(entry.notes).replace(/\s+/g, ' ').trim()}`);
    }

    if (parts.length === 0) {
      // alterlab_known_good only — not interesting to the onboarder LLM.
      continue;
    }
    // Strip any trailing period each part already carries, capitalize each
    // fragment, so the join adds exactly one separator and the line reads as
    // clean sentences ending in exactly one period.
    const sentence = parts
      .map((p) => p.replace(/\.\s*$/, ''))
      .map((p) => (p ? p[0].toUpperCase() + p.slice(1) : p))
      .join('. ');
    lines.push(`- **\`${host}\`**: ${sentence}.`);
  }
  return lines.join('\n');
}

function injectQuirks(promptTextRaw, prose) {
  const beginIdx = promptTextRaw.indexOf(VENDOR_QUIRKS_BEGIN);
  const endIdx = promptTextRaw.indexOf(VENDOR_QUIRKS_END);
  if (beginIdx === -1 || endIdx === -1 || endIdx < beginIdx) {
    throw new Error(
      `prompt is missing ${VENDOR_QUIRKS_BEGIN} / ${VENDOR_QUIRKS_END} markers`,
    );
  }
  const before = promptTextRaw.slice(0, beginIdx + VENDOR_QUIRKS_BEGIN.length);
  const after = promptTextRaw.slice(endIdx);
  return `${before}\n${prose}\n${after}`;
}

// Build the structured data the save-time gate consumes.
function buildQuirksData(registry) {
  const forceDetailBackup = [];
  const alterlabKnownGood = [];
  for (const [host, entry] of Object.entries(registry)) {
    const h = normalizeHost(host);
    if (entry && entry.force_detail_backup === true) forceDetailBackup.push(h);
    if (entry && entry.alterlab_known_good === true) alterlabKnownGood.push(h);
  }
  forceDetailBackup.sort();
  alterlabKnownGood.sort();
  return { forceDetailBackup, alterlabKnownGood };
}

try {
  // --- vendor quirks registry ---
  const quirksRaw = fs.readFileSync(quirksSrc, 'utf8');
  const registry = yaml.load(quirksRaw) || {};
  if (typeof registry !== 'object' || Array.isArray(registry)) {
    throw new Error('vendor_quirks.yaml root must be a mapping');
  }
  const normalized = {};
  for (const [k, v] of Object.entries(registry)) {
    if (v && typeof v === 'object') normalized[normalizeHost(k)] = v;
  }

  // --- prompt: inject rendered quirks between markers ---
  const promptRaw = fs.readFileSync(promptSrc, 'utf8').replace(/\r\n?/g, '\n');
  const prose = renderQuirksProse(normalized);
  const promptWithQuirks = injectQuirks(promptRaw, prose);
  const promptCode = `// AUTO-GENERATED by scripts/sync-prompt.js
// Do not edit this file directly. Edit onboard_v1.txt (and, for the vendor
// knowledge block, worker/src/product_search/vendor_quirks.yaml) instead.
export const promptText = ${JSON.stringify(promptWithQuirks)};
`;
  fs.writeFileSync(promptDest, promptCode);

  // --- vendor-quirks-data.ts for the save-time gate ---
  const data = buildQuirksData(normalized);
  const dataCode = `// AUTO-GENERATED by scripts/sync-prompt.js from worker/src/product_search/vendor_quirks.yaml.
// Do not edit directly — edit the YAML registry (ADR-068).

// Hosts (www-stripped) whose single-SKU products must carry BOTH a search URL
// and a page_type:"detail" URL — ADR-067 force_detail_backup enforcement.
export const FORCE_DETAIL_BACKUP_HOSTS: ReadonlySet<string> = new Set(${JSON.stringify(
    data.forceDetailBackup,
  )});

// Hosts (www-stripped) that AlterLab renders fine in production even when a
// bare datacenter fetch gets a 5xx / tiny body. Used by probe-url.ts to avoid
// false-negative demotions.
export const ALTERLAB_KNOWN_GOOD_HOSTS: ReadonlySet<string> = new Set(${JSON.stringify(
    data.alterlabKnownGood,
  )});
`;
  fs.writeFileSync(quirksDest, dataCode);

  console.log(
    '[sync-prompt] Synced onboard_v1.txt + vendor_quirks.yaml -> promptText.ts, vendor-quirks-data.ts',
  );
} catch (err) {
  console.error('[sync-prompt] Failed to sync:', err.message);
  process.exit(1);
}
