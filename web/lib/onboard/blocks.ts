// Shared <state> and <draft> block extractors for the onboarding chat.
//
// Phase 14: per-turn assistant messages end with two structured blocks —
//   <state>{...running decisions ledger json...}</state>
//   <draft>{...current profile intent json...}</draft>
// Everything before those blocks is the conversational reply shown to the
// user. The blocks are parsed server- and client-side: server uses <state>
// to compress the sliding window and <draft> to render YAML at save time;
// client uses <draft> to render the live YAML preview pane.

const STATE_RE = /<state>([\s\S]*?)<\/state>/i;
const DRAFT_RE = /<draft>([\s\S]*?)<\/draft>/i;

export function extractStateRaw(text: string): string | null {
  const m = STATE_RE.exec(text);
  return m ? m[1].trim() : null;
}

export function extractDraftRaw(text: string): string | null {
  const m = DRAFT_RE.exec(text);
  return m ? m[1].trim() : null;
}

function tryParseJson(raw: string): unknown | null {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function extractStateJson(text: string): Record<string, unknown> | null {
  const raw = extractStateRaw(text);
  if (!raw) return null;
  const parsed = tryParseJson(raw);
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : null;
}

export function extractDraftJson(text: string): Record<string, unknown> | null {
  const raw = extractDraftRaw(text);
  if (!raw) return null;
  const parsed = tryParseJson(raw);
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : null;
}

export function stripBlocks(text: string): string {
  return text.replace(STATE_RE, '').replace(DRAFT_RE, '').replace(/\s+$/g, '').trimEnd();
}

// Find the most-recent <state> block across an assistant-message history.
// Returns the raw JSON string (not parsed) so the caller can splice it back
// into a synthetic assistant turn verbatim.
export function findLatestStateRaw(
  messages: ReadonlyArray<{ role: string; content: string }>,
): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== 'assistant') continue;
    const raw = extractStateRaw(messages[i].content);
    if (raw) return raw;
  }
  return null;
}

// Find the most-recent <draft> block across an assistant-message history.
export function findLatestDraftJson(
  messages: ReadonlyArray<{ role: string; content: string }>,
): Record<string, unknown> | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== 'assistant') continue;
    const json = extractDraftJson(messages[i].content);
    if (json) return json;
  }
  return null;
}
