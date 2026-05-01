import { NextRequest } from 'next/server';
import OpenAI from 'openai';
import { loadOnboardPrompt } from '@/lib/onboard/prompt';

export const runtime = 'nodejs';
export const maxDuration = 60;

const PROVIDER = process.env.LLM_ONBOARD_PROVIDER ?? 'glm';
const MODEL = process.env.LLM_ONBOARD_MODEL ?? 'glm-5.1';
const MAX_TOKENS = 8192;
const MAX_TURNS_PER_REQUEST = 50;

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

export async function POST(request: NextRequest) {
  // Auth: same x-web-secret pattern as /api/dispatch. Browser sends
  // NEXT_PUBLIC_WEB_SHARED_SECRET; server checks against WEB_SHARED_SECRET.
  const expected = process.env.WEB_SHARED_SECRET;
  if (!expected) {
    return bad('WEB_SHARED_SECRET not configured on server', 500);
  }
  if (request.headers.get('x-web-secret') !== expected) {
    return bad('invalid or missing x-web-secret header', 401);
  }

  if (!process.env.GLM_API_KEY) {
    return bad('GLM_API_KEY not configured on server', 500);
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
  // Conversation must end with a user message — that's what we're answering.
  if (messages[messages.length - 1].role !== 'user') {
    return bad('last message must be from user');
  }

  const lastUserText = messages[messages.length - 1].content.toLowerCase();
  const mightSearch = lastUserText.includes('search') || lastUserText.includes('look') || lastUserText.includes('find');

  // Keep only the last 6 messages (3 turns) to prevent context ballooning.
  // Since the assistant emits the full draft profile in each turn, older turns
  // are redundant and only increase cost.
  // CRITICAL: We MUST preserve messages[0] because it contains the original
  // profile.yaml and slug when editing an existing profile!
  let trimmedMessages = messages;
  if (messages.length > 6) {
    trimmedMessages = [messages[0], ...messages.slice(-5)];
  }

  const systemPrompt = await loadOnboardPrompt();
  
  const client = new OpenAI({
    apiKey: process.env.GLM_API_KEY,
    baseURL: 'https://open.bigmodel.cn/api/paas/v4/',
  });

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (payload: unknown) => controller.enqueue(sseEncode(payload));
      try {
        let hasEmittedToolUse = false;
        if (mightSearch) {
          send({ type: 'tool_use', name: 'web_search' });
          hasEmittedToolUse = true;
        }

        const messageStream = await client.chat.completions.create({
          model: MODEL,
          max_tokens: MAX_TOKENS,
          messages: [
            { role: 'system', content: systemPrompt },
            ...trimmedMessages.map((m) => ({ role: m.role, content: m.content })),
          ],
          stream: true,
          stream_options: { include_usage: true },
          tools: [
            {
              type: 'web_search',
              web_search: {
                enable: true,
              },
            } as any,
          ],
        });

        let stopReason: string | null = null;

        for await (const chunk of messageStream) {
          if (chunk.choices && chunk.choices.length > 0) {
            const delta = chunk.choices[0].delta;
            
            // Detect Zhipu's web search tool invocation
            // Depending on the exact API response, the tool_calls might be streamed.
            if (delta.tool_calls && delta.tool_calls.length > 0) {
              const tc = delta.tool_calls[0];
              if ((tc as any).type === 'web_search' && !hasEmittedToolUse) {
                send({ type: 'tool_use', name: 'web_search' });
                hasEmittedToolUse = true;
              }
            }
            
            if (delta.content) {
              send({ type: 'delta', text: delta.content });
            }

            if (chunk.choices[0].finish_reason) {
              stopReason = chunk.choices[0].finish_reason;
            }
          }
          
          if (chunk.usage) {
            send({
              type: 'usage',
              provider: PROVIDER,
              model: MODEL,
              input_tokens: chunk.usage.prompt_tokens ?? 0,
              output_tokens: chunk.usage.completion_tokens ?? 0,
            });
          }
        }

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
