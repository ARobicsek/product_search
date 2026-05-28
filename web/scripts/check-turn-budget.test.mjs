import { test } from 'node:test';
import assert from 'node:assert';
import { shouldForceFinalize } from '../lib/onboard/turn-budget.ts';


test('shouldForceFinalize - below budget and loop limit', () => {
  const startTimeMs = 1000;
  const loopCount = 5;
  const maxLoopCount = 15;
  const budgetMs = 50000;
  const nowMs = 15000; // 14s elapsed

  assert.strictEqual(shouldForceFinalize(startTimeMs, loopCount, maxLoopCount, budgetMs, nowMs), false);
});

test('shouldForceFinalize - exceeds budget', () => {
  const startTimeMs = 1000;
  const loopCount = 5;
  const maxLoopCount = 15;
  const budgetMs = 50000;
  const nowMs = 52000; // 51s elapsed

  assert.strictEqual(shouldForceFinalize(startTimeMs, loopCount, maxLoopCount, budgetMs, nowMs), true);
});

test('shouldForceFinalize - nears loop cap', () => {
  const startTimeMs = 1000;
  const loopCount = 14; // maxLoopCount - 1
  const maxLoopCount = 15;
  const budgetMs = 50000;
  const nowMs = 15000; // 14s elapsed

  assert.strictEqual(shouldForceFinalize(startTimeMs, loopCount, maxLoopCount, budgetMs, nowMs), true);
});

test('shouldForceFinalize - both budget and loop cap exceeded', () => {
  const startTimeMs = 1000;
  const loopCount = 15;
  const maxLoopCount = 15;
  const budgetMs = 50000;
  const nowMs = 60000;

  assert.strictEqual(shouldForceFinalize(startTimeMs, loopCount, maxLoopCount, budgetMs, nowMs), true);
});
