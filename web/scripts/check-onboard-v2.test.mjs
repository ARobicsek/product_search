// Phase 34 (ADR-137) guard tests for the v2 onboarder foundation:
//   - lib/serper.ts            (Serper shopping client mappers)
//   - lib/onboard/validation-v2.ts (v2 draft validation + YAML render)
//   - lib/onboard/promptTextV2.ts  (v2 onboarder system prompt)
//
// Run via the repo's native TS test runner: `node --test --experimental-strip-types`
// (see package.json `test:guards`). These modules contain only type-strippable
// syntax + a bare `js-yaml` import, both of which load cleanly under strip-types.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  parseSerperPrice,
  mapSerperItem,
  mapSerperResponse,
  serperShoppingPreview,
} from '../lib/serper.ts';
import {
  aliasIsDistinctive,
  renderProfileV2Yaml,
  validateProfileDraftV2,
} from '../lib/onboard/validation-v2.ts';
import { promptTextV2 } from '../lib/onboard/promptTextV2.ts';

// --------------------------------------------------------------------------
// serper.ts — price parsing
// --------------------------------------------------------------------------

test('parseSerperPrice: numbers, currency strings, junk, empty', () => {
  assert.deepEqual(parseSerperPrice(599), { value: 599, text: '599' });
  assert.deepEqual(parseSerperPrice('$1,299.00'), { value: 1299.0, text: '$1,299.00' });
  assert.deepEqual(parseSerperPrice('USD 49.99'), { value: 49.99, text: 'USD 49.99' });
  assert.deepEqual(parseSerperPrice(''), { value: null, text: null });
  assert.deepEqual(parseSerperPrice(null), { value: null, text: null });
  assert.deepEqual(parseSerperPrice(undefined), { value: null, text: null });
});

// --------------------------------------------------------------------------
// serper.ts — item + response mapping
// --------------------------------------------------------------------------

test('mapSerperItem: maps fields and drops items with no title', () => {
  const mapped = mapSerperItem({
    title: 'DJI Neo 2 Motion Fly More Combo',
    source: 'B&H Photo',
    link: 'https://www.google.com/shopping/x',
    price: '$599.00',
    rating: 4.6,
    ratingCount: 120,
    productId: 'pid-1',
    imageUrl: 'https://img/x.jpg',
  });
  assert.equal(mapped?.title, 'DJI Neo 2 Motion Fly More Combo');
  assert.equal(mapped?.merchant, 'B&H Photo');
  assert.equal(mapped?.price, 599.0);
  assert.equal(mapped?.priceText, '$599.00');
  assert.equal(mapped?.rating, 4.6);
  assert.equal(mapped?.ratingCount, 120);
  assert.equal(mapped?.productId, 'pid-1');

  assert.equal(mapSerperItem({ price: 10 }), null); // no title -> dropped
});

test('mapSerperResponse: reads shopping[], dedups by productId then link', () => {
  const items = mapSerperResponse({
    shopping: [
      { title: 'A', productId: 'p1', link: 'l1' },
      { title: 'A (dup)', productId: 'p1', link: 'l2' }, // same productId -> dropped
      { title: 'B', link: 'l3' },
      { title: 'B (dup link)', link: 'l3' }, // same link -> dropped
      { title: 'C' },
    ],
  });
  assert.equal(items.length, 3);
  assert.deepEqual(items.map((i) => i.title), ['A', 'B', 'C']);
});

test('mapSerperResponse: tolerates non-object / missing shopping', () => {
  assert.deepEqual(mapSerperResponse(null), []);
  assert.deepEqual(mapSerperResponse({}), []);
  assert.deepEqual(mapSerperResponse({ shopping: 'nope' }), []);
});

test('serperShoppingPreview: empty query short-circuits without network', async () => {
  const res = await serperShoppingPreview('   ');
  assert.equal(res.ok, false);
  assert.equal(res.error, 'empty query');
  assert.equal(res.count, 0);
});

// --------------------------------------------------------------------------
// validation-v2.ts — alias distinctiveness
// --------------------------------------------------------------------------

test('aliasIsDistinctive: digit OR multi-word passes; bare generic word fails', () => {
  assert.equal(aliasIsDistinctive('Neo 2 Motion Fly More'), true); // multiword
  assert.equal(aliasIsDistinctive('CP.FP.00000273.01'), true); // digit
  assert.equal(aliasIsDistinctive('iPhone16'), true); // digit
  assert.equal(aliasIsDistinctive('drone'), false); // bare generic
  assert.equal(aliasIsDistinctive('headphones'), false);
});

// --------------------------------------------------------------------------
// validation-v2.ts — full draft validation
// --------------------------------------------------------------------------

function validDraft() {
  return {
    schema_version: 2,
    slug: 'dji-neo-2-motion-fly-more-combo',
    display_name: 'DJI Neo 2 Drone Motion Fly More Combo',
    product_type: 'drone',
    target: { unit: 'count', amount: 1 },
    queries: ['DJI Neo 2 Motion Fly More Combo'],
    match: { aliases: ['Neo 2 Motion Fly More', 'CP.FP.00000273.01'], title_excludes: ['used'], variant_strict: true },
    filters: { condition_in: ['new'], in_stock: true },
    sources: { serper: { enabled: true, gl: 'us' }, ebay: { enabled: true } },
    display: { max_listings: 20, per_vendor_cap: 3, attrs: ['price', 'condition'] },
  };
}

test('validateProfileDraftV2: a complete draft validates and renders YAML', () => {
  const res = validateProfileDraftV2(validDraft());
  assert.equal(res.ok, true, res.errors.join('; '));
  assert.equal(res.slug, 'dji-neo-2-motion-fly-more-combo');
  assert.match(res.yamlText ?? '', /schema_version: 2/);
  assert.match(res.yamlText ?? '', /queries:/);
});

test('validateProfileDraftV2: missing queries is a blocking error', () => {
  const d = validDraft();
  delete d.queries;
  const res = validateProfileDraftV2(d);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes('queries')));
});

test('validateProfileDraftV2: a generic alias is rejected', () => {
  const d = validDraft();
  d.match.aliases = ['drone'];
  const res = validateProfileDraftV2(d);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes('too generic')));
});

test('validateProfileDraftV2: invalid slug is rejected', () => {
  const d = validDraft();
  d.slug = 'Not A Slug!';
  const res = validateProfileDraftV2(d);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.toLowerCase().includes('slug')));
});

test('validateProfileDraftV2: missing display_name is rejected', () => {
  const d = validDraft();
  delete d.display_name;
  const res = validateProfileDraftV2(d);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.includes('display_name')));
});

test('validateProfileDraftV2: both sources disabled is rejected', () => {
  const d = validDraft();
  d.sources = { serper: { enabled: false }, ebay: { enabled: false } };
  const res = validateProfileDraftV2(d);
  assert.equal(res.ok, false);
  assert.ok(res.errors.some((e) => e.toLowerCase().includes('source')));
});

test('validateProfileDraftV2: missing product_type warns but does not block', () => {
  const d = validDraft();
  delete d.product_type;
  const res = validateProfileDraftV2(d);
  assert.equal(res.ok, true, res.errors.join('; '));
  assert.ok(res.warnings.some((w) => w.includes('product_type')));
});

test('validateProfileDraftV2: edit-mode pins the slug to originalSlug', () => {
  const d = validDraft();
  d.slug = 'hallucinated-new-slug';
  const res = validateProfileDraftV2(d, 'dji-neo-2-motion-fly-more-combo');
  assert.equal(res.ok, true, res.errors.join('; '));
  assert.equal(res.slug, 'dji-neo-2-motion-fly-more-combo');
});

// --------------------------------------------------------------------------
// validation-v2.ts — YAML render shape
// --------------------------------------------------------------------------

test('renderProfileV2Yaml: schema_version first, defaults target, drops empty arrays', () => {
  const yamlText = renderProfileV2Yaml({
    slug: 'x',
    display_name: 'X',
    queries: ['x'],
    vendor_allowlist: [],
  });
  assert.match(yamlText, /^schema_version: 2/m);
  assert.match(yamlText, /target:/); // defaulted
  assert.ok(!yamlText.includes('vendor_allowlist'), 'empty arrays should be dropped');
});

// --------------------------------------------------------------------------
// promptTextV2.ts — canonical v2 rules present, v1 probe apparatus absent
// --------------------------------------------------------------------------

test('promptTextV2: documents the v2 tools and rules', () => {
  for (const needle of [
    'serper_preview',
    'validate_profile',
    'web_search',
    'schema_version',
    'variant_strict',
    'vendor_allowlist',
    'Distinctive aliases',
    '<draft>',
  ]) {
    assert.ok(promptTextV2.includes(needle), `prompt should mention "${needle}"`);
  }
});

test('promptTextV2: the retired v1 probe apparatus is gone', () => {
  for (const banned of ['probe_url', 'sources_pending', 'force_detail_backup', 'alterlab']) {
    assert.ok(!promptTextV2.includes(banned), `prompt must not mention retired "${banned}"`);
  }
});
