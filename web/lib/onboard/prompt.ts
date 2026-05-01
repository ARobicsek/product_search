import 'server-only';
import { promptText } from './promptText';

let cached: Promise<string> | null = null;

export function loadOnboardPrompt(): Promise<string> {
  if (cached) return cached;
  // Mimic the old async behavior for compatibility, and clean up newlines.
  cached = Promise.resolve(promptText.replace(/\n\s*\n/g, '\n').trim());
  return cached;
}
