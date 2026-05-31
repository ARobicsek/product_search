import 'server-only';
import { promptTextV2 } from './promptTextV2';

let cached: Promise<string> | null = null;

export function loadOnboardPrompt(): Promise<string> {
  if (cached) return cached;
  // Phase 34 (ADR-137): the onboarder now serves the v2 conversational-intake
  // prompt (no vendor curation / probing / sources_pending). Mimic the old
  // async behavior for compatibility, and clean up newlines.
  cached = Promise.resolve(promptTextV2.replace(/\n\s*\n/g, '\n').trim());
  return cached;
}
