// ADR-105 search-URL render parity guard.
//
// Asserts the TS renderSearchUrl (web/lib/onboard/search-url-shared.ts)
// produces the identical search URL as the Python render_search_url
// (worker/.../vendor_quirks.py) for a SHARED set of cases. The Python half is
// pinned by worker/tests/test_search_url.py against the same fixture. If the
// two builders drift (param name, encoding), one of the two suites goes red.
//
// Run: node --test --experimental-strip-types scripts/check-search-url-parity.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { renderSearchUrl } from '../lib/onboard/search-url-shared.ts';
import { SEARCH_URL_TEMPLATES } from '../lib/onboard/vendor-quirks-data.ts';

const here = dirname(fileURLToPath(import.meta.url));
const fixturePath = resolve(here, '../../worker/tests/fixtures/search_url/cases.json');
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));

test('TS renderSearchUrl matches the parity contract fixture', () => {
  assert.ok(Array.isArray(fixture.cases) && fixture.cases.length > 0, 'fixture has no cases');
  for (const c of fixture.cases) {
    assert.strictEqual(
      renderSearchUrl(c.host, c.query, SEARCH_URL_TEMPLATES),
      c.expected_url,
      `TS renderSearchUrl diverged from the parity contract for host=${c.host} query="${c.query}"`,
    );
  }
});

test('Microcenter uses the Ntt= keyword param, not the fq=brand facet (ADR-105)', () => {
  const url = renderSearchUrl('microcenter.com', 'DJI Neo 2 Motion Fly More Combo', SEARCH_URL_TEMPLATES);
  assert.ok(url && url.includes('Ntt='), 'expected Ntt= keyword param');
  assert.ok(url && !url.includes('fq=brand'), 'must not use the fq=brand facet');
});
