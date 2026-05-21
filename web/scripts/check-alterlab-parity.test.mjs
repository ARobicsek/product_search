// T5 probe<->runtime AlterLab body parity guard (ADR-071).
//
// Asserts the TS onboarder-probe body builder (buildAlterlabBody in
// web/lib/onboard/alterlab-shared.ts) produces the byte-identical AlterLab POST
// body as the Python runtime adapter (_build_alterlab_body in
// worker/.../adapters/universal_ai.py) for a SHARED set of option cases. The
// Python half is pinned by worker/tests/test_alterlab_parity.py against the same
// fixture. If the two builders drift (as they did with the missing `asp`,
// ADR-070), one of the two suites goes red.
//
// Run: node --test --experimental-strip-types scripts/check-alterlab-parity.test.mjs
// (Node 22.16 strips the TS types from the imported .ts module at load time.)

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { buildAlterlabBody, alterlabEscalationLadder } from '../lib/onboard/alterlab-shared.ts';

const here = dirname(fileURLToPath(import.meta.url));
// web/scripts -> repo root -> worker/tests/fixtures/...
const fixturePath = resolve(here, '../../worker/tests/fixtures/alterlab_parity/body_cases.json');
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));

test('TS buildAlterlabBody matches the parity contract fixture', () => {
  assert.ok(Array.isArray(fixture.cases) && fixture.cases.length > 0, 'fixture has no cases');
  for (const c of fixture.cases) {
    const built = buildAlterlabBody(fixture.url, c.options ?? undefined);
    assert.deepStrictEqual(
      built,
      c.expected_body,
      `TS buildAlterlabBody diverged from the parity contract on case "${c.name}"`,
    );
  }
});

test('TS escalation ladder restores the documented tier-4 rung (ADR-071)', () => {
  // Plain options -> 3 rungs: base, +networkidle, +tier4.
  const ladder = alterlabEscalationLadder({ country: 'us', min_tier: 3 });
  assert.equal(ladder.length, 3);
  assert.equal(ladder[1].wait_condition, 'networkidle');
  assert.equal(ladder[2].min_tier, 4);
  // Already at tier 4 + networkidle -> no extra rung.
  assert.equal(alterlabEscalationLadder({ min_tier: 4, wait_condition: 'networkidle' }).length, 1);
});
