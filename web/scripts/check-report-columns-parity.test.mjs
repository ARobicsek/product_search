// Report-column registry anti-drift guard (ADR-097).
//
// The column set is defined in places that must agree:
//   1. worker/.../profile.py:KNOWN_REPORT_COLUMNS      (Python validator allow-list)
//   2. worker/.../synthesizer.py:COLUMN_DEFS           (Python markdown renderer)
//   3. web/lib/report-columns.ts:REPORT_COLUMN_DEFS    (TS column chooser + default set)
// The TS onboarder save-gate (schema.ts:KNOWN_REPORT_COLUMNS) DERIVES its
// allow-list from REPORT_COLUMN_IDS, so #3 is the single TS source of truth and
// the save-gate cannot drift from the chooser.
//
// ADR-094 added `price` to the Python pair but not the TS side, so the onboarder
// accepted a draft using `price` from the prompt yet rejected it at the
// save-gate. Nothing was red. This test pins the TS registry against the same
// shared fixture that worker/tests/test_synthesizer.py pins the Python half
// against; a one-sided column edit now turns one of the two suites red.
//
// NOTE: this imports report-columns.ts ONLY. schema.ts does `import
// 'server-only'`, which throws under a bare `node --test` run (the other offline
// guards avoid it for the same reason); since schema.ts derives from
// REPORT_COLUMN_IDS, checking report-columns.ts covers the save-gate too.
//
// Run: node --test --experimental-strip-types scripts/check-report-columns-parity.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { REPORT_COLUMN_IDS, DEFAULT_REPORT_COLUMNS } from '../lib/report-columns.ts';

const here = dirname(fileURLToPath(import.meta.url));
const fixturePath = resolve(here, '../../worker/tests/fixtures/report_columns/columns.json');
const contract = JSON.parse(readFileSync(fixturePath, 'utf8'));

const sorted = (xs) => [...xs].sort();

test('report-columns.ts REPORT_COLUMN_IDS matches the shared column contract', () => {
  assert.deepStrictEqual(sorted(REPORT_COLUMN_IDS), sorted(contract.columns));
});

test('report-columns.ts DEFAULT_REPORT_COLUMNS matches the shared default set', () => {
  assert.deepStrictEqual(DEFAULT_REPORT_COLUMNS, contract.default);
});
