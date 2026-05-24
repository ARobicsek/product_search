// Phase 22 onboarder-guard unit tests (offline, no live calls):
//   - ADR-079 isDetailPreferred: registry detail-preferred vendors / detail
//     page_type are flagged so the save gate keeps them on a weak probe.
//   - ADR-080 checkTitleExcludes: a title_excludes value that is a substring of
//     the product name is surfaced as a soft warning.
//
// Run: node --test --experimental-strip-types scripts/check-onboard-guards.test.mjs
// Both modules are pure (no `server-only` / Anthropic SDK / Next `@/` alias), so
// they import directly here via relative paths.

import test from 'node:test';
import assert from 'node:assert/strict';

import { isDetailPreferred } from '../lib/onboard/detail-preference.ts';
import { checkTitleExcludes } from '../lib/onboard/title-excludes-check.ts';
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
