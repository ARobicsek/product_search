// Phase 22 onboarder-guard unit tests (offline, no live calls):
//   - ADR-079 isDetailPreferred: registry detail-preferred vendors / detail
//     page_type are flagged so the save gate keeps them on a weak probe.
//   - ADR-080 checkTitleExcludes: a title_excludes value that is a substring of
//     the product name is surfaced as a soft warning.
//   - ADR-079 (Phase 27) checkDetailPreferencePresence: a URL-less placeholder
//     in sources_pending for a detail-preferred vendor is surfaced as a soft
//     warning (the LLM-drop-the-URL bypass found in Phase 26 / STRESS_TEST_26).
//
// Run: node --test --experimental-strip-types scripts/check-onboard-guards.test.mjs
// Both modules are pure (no `server-only` / Anthropic SDK / Next `@/` alias), so
// they import directly here via relative paths.

import test from 'node:test';
import assert from 'node:assert/strict';

import { isDetailPreferred } from '../lib/onboard/detail-preference.ts';
import { checkTitleExcludes } from '../lib/onboard/title-excludes-check.ts';
import { checkDetailPreferencePresence } from '../lib/onboard/detail-preference-presence.ts';
import { checkMatchAliases } from '../lib/onboard/match-aliases-check.ts';
import { checkForceDetailBackup } from '../lib/onboard/adr067-check.ts';
import {
  FORCE_DETAIL_BACKUP_HOSTS,
  PREFER_DETAIL_HOSTS,
} from '../lib/onboard/vendor-quirks-data.ts';

const detailPreferred = (url, pageType) =>
  isDetailPreferred(url, pageType, FORCE_DETAIL_BACKUP_HOSTS, PREFER_DETAIL_HOSTS);

test('ADR-079: detail page_type is always detail-preferred', () => {
  assert.equal(detailPreferred('https://example.com/p/anything', 'detail'), true);
});

test('ADR-079: a force_detail_backup / prefer_detail registry host is detail-preferred', () => {
  // Best Buy is force_detail_backup; Adorama is force_detail_backup. www- and
  // search-shaped URLs on these hosts still count (the registry, not the URL,
  // decides) so a weak probe never demotes them. (B&H was the exemplar here
  // until ADR-089 promoted it to known_failure: blocker; detail-preferred
  // status no longer applies to known_failure hosts.)
  assert.equal(detailPreferred('https://www.bestbuy.com/site/searchpage.jsp?st=sony'), true);
  assert.equal(detailPreferred('https://www.adorama.com/l/sony'), true);
});

test('ADR-079: an ordinary vendor search URL is NOT detail-preferred', () => {
  assert.equal(detailPreferred('https://www.some-random-shop.example/search?q=x', 'search'), false);
  assert.equal(detailPreferred('not a url'), false);
});

test('ADR-080: a title_excludes value that is a substring of the name warns', () => {
  const draft = {
    display_name: 'Logitech MX Master 3S',
    slug: 'logitech-mx-master-3s',
    spec_filters: [{ rule: 'title_excludes', values: ['MX Master 3'] }],
  };
  const warnings = checkTitleExcludes(draft);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0].message, /substring of the product name/);
});

test('ADR-080: a disjoint title_excludes value does NOT warn', () => {
  const draft = {
    display_name: 'Logitech MX Master 3S',
    slug: 'logitech-mx-master-3s',
    spec_filters: [{ rule: 'title_excludes', values: ['MX Master 4', '3S Lite'] }],
  };
  assert.equal(checkTitleExcludes(draft).length, 0);
});

test('ADR-080: no spec_filters / no title_excludes is a no-op', () => {
  assert.equal(checkTitleExcludes({ display_name: 'X', slug: 'x', spec_filters: [] }).length, 0);
  assert.equal(checkTitleExcludes({ display_name: 'X', slug: 'x' }).length, 0);
});

const detailPresence = (draft) =>
  checkDetailPreferencePresence(draft, FORCE_DETAIL_BACKUP_HOSTS, PREFER_DETAIL_HOSTS);

test('ADR-079 (Phase 27): URL-less placeholder for a detail-preferred host triggers warning + names the host', () => {
  // Phase 26 / stress26-mx3s live shape: the LLM dropped a detail-preferred
  // vendor's URL and left a URL-less placeholder in sources_pending with the
  // vendor name in the note. The Phase 27 reinforcement detects this and warns.
  // (Original exemplar was B&H Photo; ADR-089 promoted bhphotovideo.com to
  // known_failure: blocker, so URL-less B&H placeholders are now the EXPECTED
  // shape. Best Buy is still force_detail_backup, so the bypass shape is the
  // same and the test exercises the same code path.)
  const draft = {
    sources: [],
    sources_pending: [
      {
        id: 'universal_ai_search',
        note: 'Best Buy detail URL failed extraction on this probe; will retry search-style URL on next run',
      },
    ],
  };
  const warnings = detailPresence(draft);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0].message, /bestbuy\.com/);
  assert.match(warnings[0].message, /URL-less placeholder/i);
});

test('ADR-079 (Phase 27): URL-bearing detail-preferred source in sources does NOT trigger', () => {
  // The healthy shape: a detail-preferred vendor stays in sources with the URL
  // intact, optionally annotated with a probe_note. No URL-less placeholder
  // needed. (Was B&H; see ADR-089 — swapped to Best Buy.)
  const draftWithProbeNote = {
    sources: [
      {
        id: 'universal_ai_search',
        url: 'https://www.bestbuy.com/site/product/123-XYZ',
        page_type: 'detail',
        extra: { probe_note: 'weak probe 2026-05-25: detailExtractable=false' },
      },
    ],
    sources_pending: [],
  };
  assert.equal(detailPresence(draftWithProbeNote).length, 0);

  const draftWithoutProbeNote = {
    sources: [
      {
        id: 'universal_ai_search',
        url: 'https://www.bestbuy.com/site/product/123-XYZ',
        page_type: 'detail',
      },
    ],
    sources_pending: [],
  };
  assert.equal(detailPresence(draftWithoutProbeNote).length, 0);
});

test('ADR-079 (Phase 27): URL-less placeholder with no host signal still warns generically', () => {
  // Defense-in-depth: even when we can't infer which vendor was dropped from
  // the note text, the URL-less placeholder shape itself is the bypass we
  // refuse to ship — warn generically so the user investigates.
  const draft = {
    sources: [],
    sources_pending: [
      {
        id: 'universal_ai_search',
        note: 'probe failed; will retry next session',
      },
    ],
  };
  const warnings = detailPresence(draft);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0].message, /URL-less universal_ai_search/i);
});

test('ADR-079 (Phase 27): URL-bearing pending entry does NOT trigger', () => {
  // The ordinary-vendor demote-with-note path (different from the bypass
  // shape) keeps the URL on the pending entry — that's the existing healthy
  // gate behaviour for ordinary vendors and we don't re-flag it.
  const draft = {
    sources: [],
    sources_pending: [
      {
        id: 'universal_ai_search',
        url: 'https://www.some-random-shop.example/p/123',
        note: 'probe returned 0 candidates',
      },
    ],
  };
  assert.equal(detailPresence(draft).length, 0);
});

test('ADR-079 (Phase 27): placeholder with host already present in sources is benign', () => {
  // If the LLM emitted both a URL-bearing source AND a noisy URL-less
  // placeholder mentioning the same vendor, the vendor is already protected —
  // no warn. (Was B&H; see ADR-089 — swapped to Best Buy.)
  const draft = {
    sources: [
      {
        id: 'universal_ai_search',
        url: 'https://www.bestbuy.com/site/product/456-ABC',
        page_type: 'detail',
      },
    ],
    sources_pending: [
      {
        id: 'universal_ai_search',
        note: 'Best Buy: alternate detail URL attempted earlier — superseded',
      },
    ],
  };
  assert.equal(detailPresence(draft).length, 0);
});

// --- ADR-101 match_aliases guard ---

test('ADR-101: match_aliases check passes cleanly when aliases are present', () => {
  const draft = { display_name: 'Supermicro H14SSL-N', match_aliases: ['H14SSL-N', 'H14SSL N'] };
  assert.equal(checkMatchAliases(draft).length, 0);
});

test('ADR-101: match_aliases check returns soft warning when no aliases but confident model token exists', () => {
  const draft = { display_name: 'Supermicro H14SSL-N motherboard', match_aliases: [] };
  const warnings = checkMatchAliases(draft);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0].message, /match_aliases is empty/);
  assert.match(warnings[0].message, /'h14ssl'/);
});

test('ADR-101: match_aliases check throws error when no aliases and NO confident model token', () => {
  const draft = { display_name: 'The Economist 1yr subscription', match_aliases: [] };
  assert.throws(() => checkMatchAliases(draft), /MUST provide match_aliases/);
});

// --- ADR-098 prompt-content guards ---

import { promptText } from '../lib/onboard/promptText.ts';

test('ADR-098 fix #2: prompt contains Newegg search pattern with d= param', () => {
  assert.ok(
    promptText.includes('newegg.com/p/pl?d='),
    'prompt must include the Newegg search URL pattern with d= param',
  );
});

test('ADR-098 fix #2: prompt warns about N= category-node trap', () => {
  assert.ok(
    promptText.includes('category-node trap'),
    'prompt must warn about the N= category-node trap',
  );
});

test('ADR-098 fix #5: prompt prohibits guessing detail-URL slugs', () => {
  assert.ok(
    promptText.includes('NEVER construct a detail URL by'),
    'prompt must contain the strengthened no-guessed-slug prohibition',
  );
});

test('ADR-098 fix #1: prompt documents relevanceHits', () => {
  assert.ok(
    promptText.includes('relevanceHits'),
    'prompt must document the relevanceHits probe field',
  );
});

test('ADR-099: prompt instructs the onboarder to seed match_aliases', () => {
  assert.ok(
    promptText.includes('match_aliases'),
    'prompt must document the match_aliases field for the carry-gate',
  );
  assert.ok(
    /carry-gate/i.test(promptText),
    'prompt must explain the runtime carry-gate that match_aliases feeds',
  );
});

test('ADR-099: prompt states the carry-gate alias distinctiveness guardrail', () => {
  assert.ok(
    promptText.includes('contain a digit OR be a multi-word phrase'),
    'prompt must state the alias distinctiveness guardrail so the onboarder seeds safe aliases',
  );
});

test('ADR-100: prompt instructs to keep empty-now keyword searches', () => {
  assert.ok(
    promptText.includes('genuinely has 0 matches today'),
    'prompt must instruct to keep keyword search URLs even when they return 0 matches',
  );
  assert.ok(
    promptText.includes('DO NOT drop a working, correctly-scoped keyword search URL to `sources_pending`'),
    'prompt must forbid dropping empty search URLs',
  );
});

test('ADR-100: prompt reserves sources_pending for genuinely unreachable vendors', () => {
  assert.ok(
    promptText.includes('Narrow `sources_pending` to genuine dead-ends'),
    'prompt must instruct to reserve sources_pending for unreachable vendors',
  );
});

// --- ADR-111 hard-gate enforcement (force_detail_backup) -------------------

const forceDetailBackup = (draft) =>
  checkForceDetailBackup(draft, FORCE_DETAIL_BACKUP_HOSTS);

test('ADR-111: force_detail_backup host with search-only returns a violation', () => {
  // DJI-Neo-2 live shape (2026-05-28): amazon.com had only a search URL.
  // The check returns one entry per offending host so validation.ts can
  // route it to `errors` and 422 the save.
  const draft = {
    sources: [
      { id: 'universal_ai_search', url: 'https://www.amazon.com/s?k=DJI+Neo+2' },
    ],
  };
  const violations = forceDetailBackup(draft);
  assert.equal(violations.length, 1);
  assert.equal(violations[0].host, 'amazon.com');
  assert.match(violations[0].message, /save is BLOCKED/);
  assert.match(violations[0].message, /page_type: "detail"/);
});

test('ADR-111: force_detail_backup host with both search AND detail is clean', () => {
  const draft = {
    sources: [
      { id: 'universal_ai_search', url: 'https://www.amazon.com/s?k=DJI+Neo+2' },
      {
        id: 'universal_ai_search',
        url: 'https://www.amazon.com/dp/B0XXXXX',
        page_type: 'detail',
      },
    ],
  };
  assert.equal(forceDetailBackup(draft).length, 0);
});

test('ADR-111: force_detail_backup host with detail-only returns a violation', () => {
  const draft = {
    sources: [
      {
        id: 'universal_ai_search',
        url: 'https://www.amazon.com/dp/B0XXXXX',
        page_type: 'detail',
      },
    ],
  };
  const violations = forceDetailBackup(draft);
  assert.equal(violations.length, 1);
  assert.equal(violations[0].host, 'amazon.com');
  assert.match(violations[0].message, /Save is BLOCKED/);
  assert.match(violations[0].message, /search-style URL/);
});

test('ADR-111: a non-force_detail_backup host is unaffected', () => {
  // ebay.com is NOT in FORCE_DETAIL_BACKUP_HOSTS (marketplace with ephemeral
  // URLs — search-only is the right shape).
  const draft = {
    sources: [
      { id: 'universal_ai_search', url: 'https://www.example-vendor.com/search?q=x' },
    ],
  };
  assert.equal(forceDetailBackup(draft).length, 0);
});

test('ADR-111: multiple offending hosts each get their own violation', () => {
  // The DJI-Neo-2 live shape had amazon + target + walmart all violating.
  const draft = {
    sources: [
      { id: 'universal_ai_search', url: 'https://www.amazon.com/s?k=DJI' },
      { id: 'universal_ai_search', url: 'https://www.target.com/s?searchTerm=DJI' },
      { id: 'universal_ai_search', url: 'https://www.walmart.com/search?q=DJI' },
    ],
  };
  const violations = forceDetailBackup(draft);
  assert.equal(violations.length, 3);
  const hosts = new Set(violations.map((v) => v.host));
  assert.ok(hosts.has('amazon.com'));
  assert.ok(hosts.has('target.com'));
  assert.ok(hosts.has('walmart.com'));
});

test('ADR-111: prompt warns the LLM that validate_profile errors block save', () => {
  assert.ok(
    promptText.includes('errors` BLOCK save') ||
      promptText.includes('errors BLOCK save'),
    'prompt must teach the LLM that validate_profile errors block save',
  );
  assert.ok(
    promptText.includes('ADR-111') || promptText.includes('force_detail_backup'),
    'prompt must mention the hard force_detail_backup gate so the LLM knows the rule is enforced',
  );
});

// --- ADR-115 force_detail_backup bypass ------------------------------------
//
// validation.ts imports the `@/...` path alias, so node --test can't load it
// here without a resolver shim. Instead we pin the bypass policy by reading
// the source: the bypass branch must route ADR-111 violations to warnings
// instead of errors when bypassForceDetailBackup is set.

import { readFileSync as readFileSyncForAdr115 } from 'node:fs';
import { fileURLToPath as fileURLToPathForAdr115 } from 'node:url';
import { dirname as dirnameForAdr115, resolve as resolveForAdr115 } from 'node:path';

const __dirname_adr115 = dirnameForAdr115(fileURLToPathForAdr115(import.meta.url));
const validationSrc = readFileSyncForAdr115(
  resolveForAdr115(__dirname_adr115, '../lib/onboard/validation.ts'),
  'utf8',
);

test('ADR-115: validation.ts has a bypassForceDetailBackup option', () => {
  assert.ok(
    /bypassForceDetailBackup\??:\s*boolean/.test(validationSrc),
    'validation.ts must declare bypassForceDetailBackup on its options type',
  );
  assert.ok(
    /options\.bypassForceDetailBackup/.test(validationSrc),
    'validation.ts must branch on options.bypassForceDetailBackup',
  );
  // The bypass branch must put the violations into warnings rather than errors.
  const bypassBlock = validationSrc.match(/if\s*\(\s*options\.bypassForceDetailBackup\s*\)\s*\{[^}]*\}/);
  assert.ok(bypassBlock, 'validation.ts must have an "if (options.bypassForceDetailBackup) { ... }" block');
  assert.ok(
    /warnings\.push/.test(bypassBlock[0]),
    'the bypass branch must push the ADR-111 violations to warnings',
  );
});

test('ADR-115: save route forwards bypassForceDetailBackup to the validator', () => {
  const saveRoute = readFileSyncForAdr115(
    resolveForAdr115(__dirname_adr115, '../app/api/onboard/save/route.ts'),
    'utf8',
  );
  assert.ok(
    /body\.bypassForceDetailBackup/.test(saveRoute),
    'save route must read bypassForceDetailBackup from the request body',
  );
  assert.ok(
    /bypassForceDetailBackup\s*\}/.test(saveRoute) || /bypassForceDetailBackup,/.test(saveRoute),
    'save route must forward bypassForceDetailBackup to validateProfileDraft options',
  );
});

// --- ADR-114 draft visibility under tool-use loops -------------------------

test('ADR-114: prompt tells the LLM to emit blocks BEFORE tool_use in tool-using turns', () => {
  // Anthropic stops the assistant message at the first tool_use block, so a
  // tool-using turn that puts <state>/<draft> after the tool_use never emits
  // them. The prompt must teach the LLM to put the blocks in the text content
  // that precedes the tool_use, otherwise the right-pane preview pane stays
  // stuck on the previous turn's stub (in practice, turn 1's empty <draft>{}).
  assert.ok(
    /Anthropic ends each message at the first tool_use/.test(promptText),
    'prompt must explain Anthropic\'s tool_use message-end behavior',
  );
  assert.ok(
    /emit `<state>` and `<draft>` blocks INSIDE the text content that comes BEFORE the tool_use/.test(
      promptText,
    ),
    'prompt must instruct emitting state/draft blocks before tool_use',
  );
});
