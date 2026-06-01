import { NextRequest } from 'next/server';
import Anthropic from '@anthropic-ai/sdk';
import { loadOnboardPrompt } from '@/lib/onboard/prompt';
import { extractDraftJson, findLatestStateRaw } from '@/lib/onboard/blocks';
import { serperShoppingPreview } from '@/lib/serper';
import { validateProfileDraftV2 } from '@/lib/onboard/validation-v2';
import { shouldForceFinalize } from '@/lib/onboard/turn-budget';

export const runtime = 'edge';

// Phase 14: Anthropic Claude Haiku 4.5 with native server-side web_search +
// prompt caching on the system prompt. Replaces the GLM-5.1 OpenAI-shim
// implementation (ADR-014/-015 superseded by Phase 14 plan in PROGRESS.md).
const PROVIDER = process.env.LLM_ONBOARD_PROVIDER ?? 'anthropic';
const MODEL = process.env.LLM_ONBOARD_MODEL ?? 'claude-haiku-4-5';
// 4096 was historically hit mid-output when web_search results got inlined into
// the model's own message and it ran out of output budget BEFORE emitting the
// follow-up tool calls — leaving the loop with zero tool uses, so it exited and
// the client saw a silent halt. 8192 leaves comfortable headroom while still
// well under Haiku 4.5's per-message ceiling.
const MAX_TOKENS = 8192;
const MAX_TURNS_PER_REQUEST = 50;
// Phase 14 bench saw two consecutive vendor-discovery turns fire 3+4 searches
// each — wasteful, since the second turn's deltas were small. ADR-034
// open follow-up: tighten 5 → 2 to bound search-turn cost without losing the
// ability to cross-check 1–2 candidate vendors per turn.
const WEB_SEARCH_MAX_USES = 10;

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

        const history: Anthropic.MessageParam[] = trimmedMessages.map((m) => ({ role: m.role as 'user' | 'assistant', content: m.content }));

        let continueLoop = true;
        let loopCount = 0;
        const maxLoopCount = 15;
        const startTimeMs = Date.now();
        const ONBOARD_TURN_BUDGET_MS = process.env.ONBOARD_TURN_BUDGET_MS ? parseInt(process.env.ONBOARD_TURN_BUDGET_MS, 10) : 50000;

        while (continueLoop && loopCount < maxLoopCount) {
          loopCount++;
          continueLoop = false;

          const forceFinalize = shouldForceFinalize(startTimeMs, loopCount, maxLoopCount, ONBOARD_TURN_BUDGET_MS);
          if (forceFinalize) {
            history.push({
              role: 'user',
              content: "Time's up for this turn! Emit your best current <draft> now as-is, based on what you have so far. End your message with <state> and <draft> blocks.",
            });
            send({ type: 'status', message: 'finalizing your draft…' });
            // Deterministic signal that this turn ran out of its wall-clock
            // budget mid-research. The client uses it to offer an explicit
            // "continue" affordance instead of relying on the model to mention
            // the time-out in prose.
            send({ type: 'turn_truncated' });
          }

          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const toolsParams: any = forceFinalize ? undefined : [
            {
              type: 'web_search_20250305',
              name: 'web_search',
              max_uses: WEB_SEARCH_MAX_USES,
            },
            {
              name: 'serper_preview',
                description: 'Fire ONE real Google Shopping query (via Serper) to confirm your draft actually surfaces the target item BEFORE telling the user it is ready. Returns the top live listings (title, merchant, price). Read them: is the target present? Are obvious wrong variants caught by title_excludes? If not, adjust queries/match and you may preview once more. The same results are shown to the user. This is the only verification step — there is no per-vendor probing.',
                input_schema: {
                  type: 'object',
                  properties: {
                    query: {
                      type: 'string',
                      description: 'The search query to send to Google Shopping. Use your best drafted query for this product.',
                    },
                    gl: {
                      type: 'string',
                      description: 'Optional two-letter country code for the shopping locale (defaults to "us").',
                    },
                  },
                  required: ['query'],
                },
              },
              {
                name: 'validate_profile',
                description: 'Validates a v2 profile draft against the schema and deterministic guardrails (queries required, distinctive carry-gate aliases, a real slug, at least one enabled source). Returns any hard errors (which BLOCK save) and soft warnings. You MUST call this before telling the user the profile is ready and fix any errors it reports.',
                input_schema: {
                  type: 'object',
                  properties: {
                    draft: {
                      type: 'object',
                      description: 'The JSON draft of the v2 profile (schema_version: 2).',
                    },
                  },
                  required: ['draft'],
                },
              },
            ];

          if (loopCount > 1 && !forceFinalize) {
            send({ type: 'delta', text: '\n\n' });
          }

          const messageStream = client.messages.stream({
            model: MODEL,
            max_tokens: MAX_TOKENS,
            system: [
              {
                type: 'text',
                text: systemPrompt,
                cache_control: { type: 'ephemeral' },
              },
            ],
            ...(toolsParams && { tools: toolsParams }),
            messages: history,
          });

          for await (const event of messageStream) {
            if (event.type === 'content_block_start') {
              const block = event.content_block;
              if (block.type === 'server_tool_use' && block.name === 'web_search') {
                send({ type: 'tool_use', name: 'web_search' });
              } else if (block.type === 'tool_use' && block.name === 'serper_preview') {
                send({ type: 'tool_use', name: 'serper_preview' });
              } else if (block.type === 'tool_use' && block.name === 'validate_profile') {
                send({ type: 'tool_use', name: 'validate_profile' });
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
                outputTokens += event.usage.output_tokens ?? 0;
              }
            } else if (event.type === 'message_start') {
              const u = event.message.usage;
              inputTokens += u?.input_tokens ?? 0;
              cacheReadTokens += u?.cache_read_input_tokens ?? 0;
              cacheCreationTokens += u?.cache_creation_input_tokens ?? 0;
            }
          }

          const finalMsg = await messageStream.finalMessage();

          // Push the assistant's response to history
          history.push({
            role: 'assistant',
            content: finalMsg.content,
          });

          // ADR-114: surface the latest <draft> JSON from this iteration's
          // assistant text to the client immediately. Anthropic stops the
          // message at the first tool_use block, so during multi-turn probe
          // loops the client never sees a closing </draft> and falls back to
          // the previous turn's draft (which is often the empty turn-1 stub).
          // Streaming a draft_update event lets the right-pane preview track
          // the LLM's intent in real time, even when the final non-tool
          // message is force-finalized or truncated.
          for (const block of finalMsg.content) {
            if (block.type === 'text' && typeof block.text === 'string') {
              const draft = extractDraftJson(block.text);
              if (draft) {
                send({ type: 'draft_update', draft });
                break;
              }
            }
          }

          // Check if there are tool uses of any kind
          const allToolUses = finalMsg.content.filter(
            (block: { type: string; name?: string; id?: string; input?: unknown }) => block.type === 'tool_use'
          );

          if (allToolUses.length > 0) {
            continueLoop = true;
            const toolResultsContent = await Promise.all(
              allToolUses.map(async (toolUse: { type: string; name?: string; id?: string; input?: unknown }) => {
                if (toolUse.type !== 'tool_use') return null;

                const toolUseId = toolUse.id;
                
                let resultText = '';
                if (toolUse.name === 'serper_preview') {
                  const input = toolUse.input as { query?: string; gl?: string };
                  const query = typeof input.query === 'string' ? input.query : '';
                  const gl = typeof input.gl === 'string' ? input.gl : undefined;

                  // Detailed tool_use event so the client can show which query
                  // is being previewed.
                  send({ type: 'tool_use', name: 'serper_preview', input });

                  const r = await serperShoppingPreview(query, { gl });
                  // Stream the live results to the user pane (both the model and
                  // the user confirm the query surfaces the item together).
                  send({
                    type: 'serper_preview',
                    query: r.query,
                    ok: r.ok,
                    count: r.count,
                    items: r.items.slice(0, 12),
                    error: r.error,
                  });

                  // Compact text summary back to the model so it can judge
                  // target-present + title_excludes coverage.
                  if (!r.ok) {
                    resultText =
                      `serper_preview FAILED for query "${r.query}": ${r.error ?? 'unknown error'}. ` +
                      `The shopping index may be unreachable right now — you can proceed with your best ` +
                      `draft and tell the user the live preview couldn't run this time.`;
                  } else if (r.count === 0) {
                    resultText =
                      `serper_preview for "${r.query}" returned 0 results. The query may be too specific ` +
                      `or mis-scoped — consider broadening it, then preview once more.`;
                  } else {
                    const top = r.items.slice(0, 20);
                    const lines = top.map((it) => {
                      const price = it.priceText ?? (it.price != null ? `$${it.price}` : '—');
                      return `${it.title} | ${it.merchant ?? '—'} | ${price}`;
                    });
                    resultText =
                      `serper_preview for "${r.query}" returned ${r.count} results (top ${top.length}):\n` +
                      lines.join('\n') +
                      `\n\nCheck: is the target present? Are obvious wrong variants caught by title_excludes?`;
                  }
                } else if (toolUse.name === 'validate_profile') {
                  const input = toolUse.input as { draft: Record<string, unknown> };
                  const draft = input.draft;

                  // The validate_profile draft argument is the authoritative
                  // current draft (ADR-114). Surface it to the client so the
                  // right-pane preview reflects the model's working state even
                  // when the final non-tool emission of <draft> never lands.
                  if (draft && typeof draft === 'object' && !Array.isArray(draft)) {
                    send({ type: 'draft_update', draft });
                  }

                  try {
                    // Advisory check during the interview (edit-mode slug pin
                    // happens authoritatively at save). originalSlug = null here.
                    const validationRes = validateProfileDraftV2(draft, null);
                    resultText = JSON.stringify({
                      ok: validationRes.ok,
                      errors: validationRes.errors,
                      warnings: validationRes.warnings,
                    }, null, 2);
                  } catch (err) {
                    resultText = JSON.stringify({
                      ok: false,
                      errors: [err instanceof Error ? err.message : String(err)],
                      warnings: [],
                    }, null, 2);
                  }
                } else if (toolUse.name === 'web_search') {
                  resultText = "SYSTEM ERROR: You attempted to use web_search in parallel with another tool. Anthropic server-side search cannot be used in parallel with client tools. Please emit web_search alone, wait for the result, and then use other tools.";
                } else {
                  resultText = `SYSTEM ERROR: Unknown tool ${toolUse.name}`;
                }

                return {
                  type: 'tool_result',
                  tool_use_id: toolUseId,
                  content: resultText,
                };
              })
            );

            history.push({
              role: 'user',
              content: toolResultsContent.filter(Boolean) as Anthropic.ToolResultBlockParam[],
            });
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
