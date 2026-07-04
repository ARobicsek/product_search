// Guard tests for lib/profile-summary.ts — the pure, read-only summarizer that
// powers the zero-cost profile viewer (no LLM). Run via the repo's native TS
// test runner: `node --test --experimental-strip-types` (see package.json
// `test:guards`). Only type-strippable syntax + a bare `js-yaml` import.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { summarizeProfile, prettifyKey } from '../lib/profile-summary.ts';

const DJI_YAML = `schema_version: 2
slug: "dji-neo-2-motion-fly-more-combo"
display_name: "DJI Neo 2 Motion Fly More Combo"
description: "Palm-sized 4K drone bundle."
product_type: "drone"
target:
  unit: "unit"
  amount: 1
queries:
  - "DJI Neo 2 Motion Fly More Combo"
match:
  aliases:
    - "DJI Neo 2"
    - "Neo 2 Motion Fly More"
  title_excludes:
    - "Refurbished"
    - "Used"
  variant_strict: true
filters:
  condition_in: ["new"]
  in_stock: true
flags:
  - low_seller_feedback: { rating_pct_below: 98, count_below: 50 }
sources:
  serper: { enabled: true, gl: us, num: 40 }
  ebay: { enabled: true }
vendor_allowlist: []
vendor_blocklist: []
display:
  max_listings: 20
  per_vendor_cap: 3
  attrs: ["price", "condition", "seller"]
alerts:
  - kind: price_below
    threshold_usd: 550
    mode: is_below
  - kind: new_vendor_carries
`;

test('summarizeProfile: parses the canonical v2 fixture', () => {
  const s = summarizeProfile(DJI_YAML);
  assert.equal(s.ok, true);
  assert.equal(s.parseError, null);
  assert.equal(s.displayName, 'DJI Neo 2 Motion Fly More Combo');
  assert.equal(s.slug, 'dji-neo-2-motion-fly-more-combo');
  assert.equal(s.productType, 'drone');
  assert.equal(s.schemaVersion, 2);
  assert.equal(s.target, '1 unit');
  assert.deepEqual(s.queries, ['DJI Neo 2 Motion Fly More Combo']);
  assert.deepEqual(s.aliases, ['DJI Neo 2', 'Neo 2 Motion Fly More']);
  assert.deepEqual(s.titleExcludes, ['Refurbished', 'Used']);
  assert.equal(s.variantStrict, true);
  assert.deepEqual(s.displayAttrs, ['price', 'condition', 'seller']);
  assert.equal(s.maxListings, 20);
  assert.equal(s.perVendorCap, 3);
});

test('summarizeProfile: friendly filters', () => {
  assert.deepEqual(summarizeProfile(DJI_YAML).filters, ['New only', 'In stock only']);

  const multi = summarizeProfile(
    'schema_version: 2\nslug: x\ndisplay_name: X\nqueries: ["q"]\nfilters:\n  condition_in: ["new", "used", "refurbished"]\n  min_quantity: 5\n',
  );
  assert.deepEqual(multi.filters, ['New, used or refurbished', 'At least 5 available']);

  const none = summarizeProfile('schema_version: 2\nslug: x\ndisplay_name: X\nqueries: ["q"]\n');
  assert.deepEqual(none.filters, []);
});

test('summarizeProfile: sources reflect enabled state + defaults', () => {
  const s = summarizeProfile(DJI_YAML);
  const byLabel = Object.fromEntries(s.sources.map((x) => [x.label, x]));
  assert.equal(byLabel['Google Shopping (Serper)'].enabled, true);
  assert.equal(byLabel['Google Shopping (Serper)'].detail, 'US · 40 results');
  assert.equal(byLabel['eBay'].enabled, true);
  assert.equal(byLabel['Amazon'].enabled, false);

  // Serper defaults to enabled when the block is absent; ebay/amazon default off.
  const bare = summarizeProfile('schema_version: 2\nslug: x\ndisplay_name: X\nqueries: ["q"]\n');
  const bareByLabel = Object.fromEntries(bare.sources.map((x) => [x.label, x]));
  assert.equal(bareByLabel['Google Shopping (Serper)'].enabled, true);
  assert.equal(bareByLabel['eBay'].enabled, false);
  assert.equal(bareByLabel['Amazon'].enabled, false);
});

test('summarizeProfile: flags rendered with params', () => {
  assert.deepEqual(summarizeProfile(DJI_YAML).flags, [
    'Low seller feedback (rating pct below 98, count below 50)',
  ]);
});

test('summarizeProfile: target pluralization', () => {
  const eight = summarizeProfile(
    'schema_version: 2\nslug: x\ndisplay_name: X\nqueries: ["q"]\ntarget:\n  unit: module\n  amount: 8\n',
  );
  assert.equal(eight.target, '8 modules');
});

test('summarizeProfile: malformed YAML surfaces parseError but keeps raw', () => {
  const bad = 'schema_version: 2\n  : : bad indent\n\t- nope';
  const s = summarizeProfile(bad);
  assert.equal(s.ok, false);
  assert.ok(s.parseError);
  assert.equal(s.raw, bad);
});

test('summarizeProfile: null / empty input', () => {
  const s = summarizeProfile(null);
  assert.equal(s.ok, false);
  assert.equal(s.parseError, 'No profile found.');
  assert.equal(s.raw, '');
});

test('prettifyKey', () => {
  assert.equal(prettifyKey('low_seller_feedback'), 'Low seller feedback');
  assert.equal(prettifyKey('new'), 'New');
});
