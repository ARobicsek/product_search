import { NextRequest } from 'next/server';
import Anthropic from '@anthropic-ai/sdk';
import { loadOnboardPrompt } from '@/lib/onboard/prompt';
import { findLatestStateRaw } from '@/lib/onboard/blocks';

export const runtime = 'edge';

// Phase 14: Anthropic Claude Haiku 4.5 with native server-side web_search +
// prompt caching on the system prompt. Replaces the GLM-5.1 OpenAI-shim
// implementation (ADR-014/-015 superseded by Phase 14 plan in PROGRESS.md).
const PROVIDER = process.env.LLM_ONBOARD_PROVIDER ?? 'anthropic';
const MODEL = process.env.LLM_ONBOARD_MODEL ?? 'claude-haiku-4-5';
const MAX_TOKENS = 4096;
const MAX_TURNS_PER_REQUEST = 50;
const WEB_SEARCH_MAX_USES = 5;

// Sliding-window policy:
//   * Always preserve messages[0] (kickoff — contains slug/profile.yaml in
//     edit mode, the user's original product request in new-profile mode).
//   * Always preserve the last 4 turns of conversation (≈2 user/assistant
//     exchanges) so the model has fresh context for what was just asked.
//   * Replace the dropped middle turns with one synthetic assistant turn
//     containing the latest <state>{...}</state> block. This is the
//     decisions-ledger pattern — the model sees a compact summary of every
//     decision it has made so far, so "what slug did we agree on?" never
//     becomes a memory failure.
const KEEP_HEAD = 1;
const KEEP_TAIL = 4;

interface IncomingMessage {
  role: 'user' | 'assistant';
  content: string;
}

function bad(reason: string, status = 400) {
  return Response.json({ ok: false, error: reason }, { status });
}

function sseEncode(payload: unknown): Uint8Array {
  return new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`);
}

// Compress the message list: head + synthetic ledger turn + tail. Returns
// the trimmed list; if no compression is needed, returns the original.
function compressWithLedger(messages: IncomingMessage[]): IncomingMessage[] {
  if (messages.length <= KEEP_HEAD + KEEP_TAIL) return messages;
  const head = messages.slice(0, KEEP_HEAD);
  const tail = messages.slice(messages.length - KEEP_TAIL);
  const middle = messages.slice(KEEP_HEAD, messages.length - KEEP_TAIL);
  const ledgerRaw = findLatestStateRaw(middle) ?? findLatestStateRaw(tail);
  if (!ledgerRaw) return [...head, ...tail];
  const synthetic: IncomingMessage = {
    role: 'assistant',
    content: `[Earlier turns elided. Decisions confirmed so far:]\n<state>${ledgerRaw}</state>`,
  };
  return [...head, synthetic, ...tail];
}

export async function POST(request: NextRequest) {
  // Auth: same x-web-secret pattern as /api/dispatch.
  const expected = process.env.WEB_SHARED_SECRET;
  if (!expected) {
    return bad('WEB_SHARED_SECRET not configured on server', 500);
  }
  if (request.headers.get('x-web-secret') !== expected) {
    return bad('invalid or missing x-web-secret header', 401);
  }

  if (!process.env.ANTHROPIC_API_KEY) {
    return bad('ANTHROPIC_API_KEY not configured on server', 500);
  }

  let body: { messages?: unknown };
  try {
    body = await request.json();
  } catch {
    return bad('invalid JSON body');
  }
  if (!Array.isArray(body.messages) || body.messages.length === 0) {
    return bad('messages must be a non-empty array');
  }
  if (body.messages.length > MAX_TURNS_PER_REQUEST) {
    return bad(`messages exceeds ${MAX_TURNS_PER_REQUEST}-turn limit`);
  }
  const messages: IncomingMessage[] = [];
  for (const m of body.messages as Array<unknown>) {
    if (typeof m !== 'object' || m === null) return bad('each message must be an object');
    const mm = m as Record<string, unknown>;
    if (mm.role !== 'user' && mm.role !== 'assistant') {
      return bad('message.role must be "user" or "assistant"');
    }
    if (typeof mm.content !== 'string' || mm.content.length === 0) {
      return bad('message.content must be a non-empty string');
    }
    messages.push({ role: mm.role, content: mm.content });
  }
  if (messages[messages.length - 1].role !== 'user') {
    return bad('last message must be from user');
  }

  const trimmedMessages = compressWithLedger(messages);
  const systemPrompt = await loadOnboardPrompt();

  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (payload: unknown) => controller.enqueue(sseEncode(payload));
      try {
        let inputTokens = 0;
        let outputTokens = 0;
        let cacheReadTokens = 0;
        let cacheCreationTokens = 0;
        let stopReason: string | null = null;

        const messageStream = client.messages.stream({
          model: MODEL,
          max_tokens: MAX_TOKENS,
          // Cache the system prompt — it's ~7KB of stable schema docs and
          // doesn't change between turns. Cuts repeat-turn input cost ~90%
          // (Anthropic charges 0.1× input rate for cache reads).
          system: [
            {
              type: 'text',
              text: systemPrompt,
              cache_control: { type: 'ephemeral' },
            },
          ],
          // Server-side web search — Anthropic runs the search and feeds
          // results back into the model in the same streaming response, so
          // we don't need multi-turn tool roundtripping on our end.
          tools: [
            {
              type: 'web_search_20250305',
              name: 'web_search',
              max_uses: WEB_SEARCH_MAX_USES,
            },
          ],
          messages: trimmedMessages.map((m) => ({ role: m.role, content: m.content })),
        });

        for await (const event of messageStream) {
          if (event.type === 'content_block_start') {
            const block = event.content_block;
            // Server-tool invocation — surface as a "Searching the web…"
            // status to the user.
            if (block.type === 'server_tool_use' && block.name === 'web_search') {
              send({ type: 'tool_use', name: 'web_search' });
            }
          } else if (event.type === 'content_block_delta') {
            const delta = event.delta;
            if (delta.type === 'text_delta' && delta.text) {
              send({ type: 'delta', text: delta.text });
            }
          } else if (event.type === 'message_delta') {
            if (event.delta.stop_reason) {
              stopReason = event.delta.stop_reason;
            }
            if (event.usage) {
              outputTokens = event.usage.output_tokens ?? outputTokens;
            }
          } else if (event.type === 'message_start') {
            const u = event.message.usage;
            inputTokens = u?.input_tokens ?? 0;
            cacheReadTokens = u?.cache_read_input_tokens ?? 0;
            cacheCreationTokens = u?.cache_creation_input_tokens ?? 0;
          }
        }

        send({
          type: 'usage',
          provider: PROVIDER,
          model: MODEL,
          input_tokens: inputTokens,
          output_tokens: outputTokens,
          cache_read_tokens: cacheReadTokens,
          cache_creation_tokens: cacheCreationTokens,
        });
        send({ type: 'done', stopReason });
      } catch (err) {
        const message = err instanceof Error ? err.message : 'onboarding LLM call failed';
        send({ type: 'error', error: message });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
    },
  });
}
