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
  // B&H is prefer_page_type:detail; Best Buy is force_detail_backup. www- and
  // search-shaped URLs on these hosts still count (the registry, not the URL,
  // decides) so a weak probe never demotes them.
  assert.equal(detailPreferred('https://www.bhphotovideo.com/c/search?q=sony'), true);
  assert.equal(detailPreferred('https://www.bestbuy.com/site/searchpage.jsp?st=sony'), true);
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

test('ADR-079 (Phase 27): URL-less placeholder for B&H Photo triggers warning + names the host', () => {
  // Phase 26 / stress26-mx3s live shape: the LLM dropped the B&H detail URL
  // and left a URL-less placeholder in sources_pending with the vendor name in
  // the note. The Phase 27 reinforcement detects this and warns.
  const draft = {
    sources: [],
    sources_pending: [
      {
        id: 'universal_ai_search',
        note: 'B&H Photo detail URL failed extraction on this probe; will retry search-style URL on next run',
      },
    ],
  };
  const warnings = detailPresence(draft);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0].message, /bhphotovideo\.com/);
  assert.match(warnings[0].message, /URL-less placeholder/i);
});

test('ADR-079 (Phase 27): URL-bearing B&H detail source in sources does NOT trigger', () => {
  // The healthy shape: B&H stays in sources with the URL intact, optionally
  // annotated with a probe_note. No URL-less placeholder needed.
  const draftWithProbeNote = {
    sources: [
      {
        id: 'universal_ai_search',
        url: 'https://www.bhphotovideo.com/c/product/123-XYZ',
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
        url: 'https://www.bhphotovideo.com/c/product/123-XYZ',
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
  // If the LLM emitted both a URL-bearing B&H source AND a noisy URL-less
  // placeholder mentioning B&H, the vendor is already protected — no warn.
  const draft = {
    sources: [
      {
        id: 'universal_ai_search',
        url: 'https://www.bhphotovideo.com/c/product/456-ABC',
        page_type: 'detail',
      },
    ],
    sources_pending: [
      {
        id: 'universal_ai_search',
        note: 'B&H Photo: alternate detail URL attempted earlier — superseded',
      },
    ],
  };
  assert.equal(detailPresence(draft).length, 0);
});
