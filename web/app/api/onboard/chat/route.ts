import { NextRequest } from 'next/server';
import Anthropic from '@anthropic-ai/sdk';
import { loadOnboardPrompt } from '@/lib/onboard/prompt';

export const runtime = 'nodejs';
export const maxDuration = 60;

const MODEL = process.env.LLM_ONBOARD_MODEL ?? 'claude-sonnet-4-6';
const MAX_TOKENS = 8192;
const MAX_WEB_SEARCHES = 5;
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
  // Conversation must end with a user message — that's what we're answering.
  if (messages[messages.length - 1].role !== 'user') {
    return bad('last message must be from user');
  }

  const systemPrompt = await loadOnboardPrompt();
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (payload: unknown) => controller.enqueue(sseEncode(payload));
      try {
        const messageStream = client.messages.stream({
          model: MODEL,
          max_tokens: MAX_TOKENS,
          system: systemPrompt,
          messages: messages.map((m) => ({ role: m.role, content: m.content })),
          tools: [
            {
              type: 'web_search_20260209',
              name: 'web_search',
              max_uses: MAX_WEB_SEARCHES,
            },
          ],
        });

        messageStream.on('text', (delta: string) => {
          if (delta) send({ type: 'delta', text: delta });
        });

        messageStream.on('streamEvent', (ev) => {
          if (
            ev.type === 'content_block_start' &&
            ev.content_block?.type === 'server_tool_use' &&
            ev.content_block.name === 'web_search'
          ) {
            send({ type: 'tool_use', name: 'web_search' });
          }
        });

        const final = await messageStream.finalMessage();
        send({ type: 'done', stopReason: final.stop_reason ?? null });
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
