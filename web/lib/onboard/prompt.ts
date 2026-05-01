import 'server-only';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

// Canonical location is worker/src/product_search/onboarding/prompts/onboard_v1.txt,
// per docs/LLM_STRATEGY.md hard rule #3 (prompts are checked-in files, not strings
// in code). next.config.ts adds this file to outputFileTracingIncludes so Vercel's
// monorepo tracing copies it into the deployment bundle.
const PROMPT_REL_PATH = path.join(
  '..',
  'worker',
  'src',
  'product_search',
  'onboarding',
  'prompts',
  'onboard_v1.txt',
);

let cached: Promise<string> | null = null;

export function loadOnboardPrompt(): Promise<string> {
  if (cached) return cached;
  cached = readFile(path.join(process.cwd(), PROMPT_REL_PATH), 'utf-8').then(
    (text) => text.replace(/\n\s*\n/g, '\n').trim()
  );
  return cached;
}
