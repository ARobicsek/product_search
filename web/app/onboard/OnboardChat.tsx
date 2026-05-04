'use client';

import { FormEvent, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Bot,
  CheckCircle2,
  DollarSign,
  Loader2,
  Save,
  Search,
  Send,
  Trash2,
  User,
} from 'lucide-react';
import { estimateCostUsd, formatCostUsd } from '@/lib/llm-prices';
import { extractDraftJson, stripBlocks } from '@/lib/onboard/blocks';
import { renderProfileYaml } from '@/lib/onboard/render-yaml';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'success'; slug: string; commitUrl: string | null }
  | { kind: 'error'; message: string; details?: string[] };

function getKickoff(initialProfile?: string | null): ChatMessage {
  if (!initialProfile) {
    return {
      role: 'user',
      content: "Hi — I'd like to onboard a new product to track. Please ask me your first question.",
    };
  }
  return {
    role: 'user',
    content: `Hi — I'd like to edit an existing product profile. Here is the current draft:\n\n\`\`\`yaml\n${initialProfile}\n\`\`\`\n\nPlease acknowledge this profile and ask me what I would like to change.`,
  };
}

function findLatestDraft(messages: ChatMessage[]): Record<string, unknown> | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== 'assistant') continue;
    const j = extractDraftJson(messages[i].content);
    if (j) return j;
  }
  return null;
}

function safeRender(intent: Record<string, unknown> | null): string | null {
  if (!intent) return null;
  try {
    return renderProfileYaml(intent);
  } catch {
    return null;
  }
}

export function OnboardChat({ initialProfile, initialSlug }: { initialProfile?: string | null, initialSlug?: string | null }) {
  const router = useRouter();
  const kickoffMessage = getKickoff(initialProfile);
  const [messages, setMessages] = useState<ChatMessage[]>([kickoffMessage]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [statusLine, setStatusLine] = useState<string>('');
  const [error, setError] = useState<string>('');
  const [saveState, setSaveState] = useState<SaveState>({ kind: 'idle' });
  const [sessionUsage, setSessionUsage] = useState({
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    cacheCreationTokens: 0,
    turns: 0,
    provider: 'anthropic',
    model: '',
  });
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const cancelled = useRef(false);

  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, statusLine]);

  useEffect(() => {
    void runTurn([kickoffMessage]);
    return () => {
      cancelled.current = true;
    };
  }, []);

  async function runTurn(history: ChatMessage[]) {
    setStreaming(true);
    setError('');
    setStatusLine('');
    setMessages([...history, { role: 'assistant', content: '' }]);

    const secret = process.env.NEXT_PUBLIC_WEB_SHARED_SECRET ?? '';
    let response: Response;
    try {
      response = await fetch('/api/onboard/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-web-secret': secret },
        body: JSON.stringify({ messages: history }),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'network error');
      setStreaming(false);
      return;
    }

    if (!response.ok || !response.body) {
      const txt = await response.text().catch(() => '');
      setError(`chat request failed: ${response.status} ${txt.slice(0, 200)}`);
      setStreaming(false);
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done || cancelled.current) break;
      buffer += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const line = raw.trim();
        if (!line.startsWith('data:')) continue;
        const json = line.slice(5).trim();
        if (!json) continue;
        let payload: {
          type?: string;
          text?: string;
          error?: string;
          provider?: string;
          model?: string;
          input_tokens?: number;
          output_tokens?: number;
          cache_read_tokens?: number;
          cache_creation_tokens?: number;
        };
        try {
          payload = JSON.parse(json);
        } catch {
          continue;
        }
        if (payload.type === 'delta' && typeof payload.text === 'string') {
          setStatusLine('');
          setMessages((prev) => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.role === 'assistant') {
              copy[copy.length - 1] = { role: 'assistant', content: last.content + payload.text! };
            }
            return copy;
          });
        } else if (payload.type === 'tool_use') {
          setStatusLine('Searching the web…');
        } else if (payload.type === 'usage') {
          setSessionUsage((u) => ({
            inputTokens: u.inputTokens + (payload.input_tokens ?? 0),
            outputTokens: u.outputTokens + (payload.output_tokens ?? 0),
            cacheReadTokens: u.cacheReadTokens + (payload.cache_read_tokens ?? 0),
            cacheCreationTokens: u.cacheCreationTokens + (payload.cache_creation_tokens ?? 0),
            turns: u.turns + 1,
            provider: payload.provider ?? u.provider,
            model: payload.model ?? u.model,
          }));
        } else if (payload.type === 'error') {
          setError(payload.error ?? 'unknown error');
        } else if (payload.type === 'done') {
          // Stream finished cleanly.
        }
      }
    }
    setStreaming(false);
    setStatusLine('');
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (streaming) return;
    const text = input.trim();
    if (!text) return;
    setInput('');
    const next: ChatMessage[] = [...messages, { role: 'user', content: text }];
    await runTurn(next);
  }

  function onReset() {
    if (streaming) return;
    setMessages([kickoffMessage]);
    setSaveState({ kind: 'idle' });
    setError('');
    setSessionUsage({
      inputTokens: 0,
      outputTokens: 0,
      cacheReadTokens: 0,
      cacheCreationTokens: 0,
      turns: 0,
      provider: 'anthropic',
      model: '',
    });
    void runTurn([kickoffMessage]);
  }

  const draftIntent = findLatestDraft(messages);
  const draftYaml = safeRender(draftIntent);
  const draftSlug = (draftIntent?.slug as string | undefined) ?? null;

  async function onSave() {
    if (!draftIntent || saveState.kind === 'saving') return;
    setSaveState({ kind: 'saving' });

    const secret = process.env.NEXT_PUBLIC_WEB_SHARED_SECRET ?? '';
    try {
      const res = await fetch('/api/onboard/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-web-secret': secret },
        body: JSON.stringify({ draft: draftIntent, originalSlug: initialSlug }),
      });
      const data = (await res.json()) as {
        ok: boolean;
        slug?: string;
        commitUrl?: string | null;
        error?: string;
        details?: string[];
        probeReports?: Array<{
          url: string;
          ok: boolean;
          jsonldCount: number;
          anchorCount: number;
          reason: string | null;
        }>;
      };
      // Phase 15: surface probe failures in the error path (so the user
      // can see why ``sources`` ended up too thin to validate). On the
      // success path, demotions are silent — they're recorded in the
      // committed YAML's ``sources_pending`` block and the user will see
      // them when they read the saved profile.
      const demotions = (data.probeReports ?? []).filter((r) => !r.ok);
      if (!res.ok || !data.ok || !data.slug) {
        const probeMessages = demotions.map(
          (r) => `${r.url}: ${r.reason ?? 'probe returned 0 candidates'}`,
        );
        setSaveState({
          kind: 'error',
          message: data.error ?? `Save failed (${res.status})`,
          details: [...(data.details ?? []), ...probeMessages],
        });
        return;
      }
      if (demotions.length > 0) {
        console.warn(
          `[onboard/save] ${demotions.length} universal_ai URL(s) demoted to sources_pending:`,
          demotions,
        );
      }
      setSaveState({ kind: 'success', slug: data.slug, commitUrl: data.commitUrl ?? null });
      setTimeout(() => router.push(`/${data.slug}`), 800);
    } catch (err) {
      setSaveState({
        kind: 'error',
        message: err instanceof Error ? err.message : 'save failed',
      });
    }
  }

  return (
    <div className="flex-1 flex flex-col md:grid md:grid-cols-[1fr_minmax(0,28rem)] md:gap-4 max-w-6xl w-full mx-auto p-3 sm:p-4 min-h-0">
      {/* Transcript */}
      <section className="flex flex-col min-h-0 bg-white dark:bg-gray-950 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
        <div
          ref={transcriptRef}
          className="flex-1 overflow-y-auto p-3 sm:p-4 space-y-4 min-h-[40vh] md:min-h-0"
        >
          {messages.map((m, i) => (
            <MessageBubble key={i} role={m.role} content={m.content} />
          ))}
          {streaming && statusLine && (
            <div className="flex items-center text-xs text-gray-500 gap-2 pl-9">
              <Search className="w-3.5 h-3.5 animate-pulse" />
              <span>{statusLine}</span>
            </div>
          )}
          {error && (
            <div className="text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-900/40 rounded-lg px-3 py-2">
              {error}
            </div>
          )}
        </div>
        <form
          onSubmit={onSubmit}
          className="border-t border-gray-200 dark:border-gray-800 p-2 sm:p-3 flex gap-2 bg-white dark:bg-gray-950"
        >
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={streaming ? 'Waiting for reply…' : 'Type your answer'}
            disabled={streaming}
            className="flex-1 rounded-full border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 px-4 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={streaming || !input.trim()}
            className="flex items-center justify-center w-10 h-10 rounded-full bg-blue-600 text-white disabled:bg-gray-300 disabled:dark:bg-gray-700 disabled:cursor-not-allowed hover:bg-blue-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Send"
          >
            {streaming ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </button>
          <button
            type="button"
            onClick={onReset}
            disabled={streaming}
            className="flex items-center justify-center w-10 h-10 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 disabled:opacity-50 hover:bg-gray-200 dark:hover:bg-gray-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
            aria-label="Restart conversation"
            title="Restart"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </form>
      </section>

      {/* Draft profile preview */}
      <aside className="mt-3 md:mt-0 flex flex-col min-h-0 bg-white dark:bg-gray-950 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Draft profile</h2>
          {draftSlug && (
            <span className="text-[11px] text-gray-500 truncate ml-2">{draftSlug}</span>
          )}
        </div>
        <div className="flex-1 overflow-y-auto">
          {draftYaml ? (
            <pre className="p-3 sm:p-4 text-[11px] leading-snug font-mono text-gray-800 dark:text-gray-200 whitespace-pre-wrap wrap-break-word">
              {draftYaml}
            </pre>
          ) : (
            <div className="p-6 text-xs text-gray-500 dark:text-gray-400 text-center">
              The draft will appear here once the interview gets going.
            </div>
          )}
        </div>
        <div className="border-t border-gray-200 dark:border-gray-800 p-3 space-y-2 bg-gray-50 dark:bg-gray-900/50">
          {sessionUsage.turns > 0 && (
            <SessionCost
              inputTokens={sessionUsage.inputTokens}
              outputTokens={sessionUsage.outputTokens}
              cacheReadTokens={sessionUsage.cacheReadTokens}
              cacheCreationTokens={sessionUsage.cacheCreationTokens}
              turns={sessionUsage.turns}
              provider={sessionUsage.provider}
              model={sessionUsage.model}
            />
          )}
          {saveState.kind === 'error' && (
            <div className="text-[11px] text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-900/40 rounded-lg px-3 py-2 space-y-1">
              <div className="font-medium">{saveState.message}</div>
              {saveState.details && saveState.details.length > 0 && (
                <ul className="list-disc ml-4 space-y-0.5">
                  {saveState.details.slice(0, 8).map((d, i) => (
                    <li key={i}>{d}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {saveState.kind === 'success' && (
            <div className="flex items-center gap-2 text-[11px] text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-900/40 rounded-lg px-3 py-2">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>
                Saved <strong>{saveState.slug}</strong>. Opening the page…
              </span>
            </div>
          )}
          <button
            type="button"
            onClick={onSave}
            disabled={!draftYaml || saveState.kind === 'saving' || saveState.kind === 'success' || streaming}
            className="w-full flex items-center justify-center gap-2 rounded-lg bg-blue-600 text-white text-sm font-medium px-4 py-2 disabled:bg-gray-300 disabled:dark:bg-gray-700 disabled:cursor-not-allowed hover:bg-blue-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {saveState.kind === 'saving' ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {saveState.kind === 'saving' ? 'Validating + committing…' : 'Save profile to repo'}
          </button>
        </div>
      </aside>
    </div>
  );
}

function SessionCost({
  inputTokens,
  outputTokens,
  cacheReadTokens,
  cacheCreationTokens,
  turns,
  provider,
  model,
}: {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  turns: number;
  provider: string;
  model: string;
}) {
  // Anthropic prompt-cache pricing: cache reads cost 0.1× input rate, cache
  // creation costs 1.25× input rate. Apply those multipliers to the stock
  // input price the LLM_PRICING table already exposes.
  const baseCost = model
    ? estimateCostUsd(provider, model, inputTokens, outputTokens)
    : null;
  const cacheReadCost = model
    ? (estimateCostUsd(provider, model, cacheReadTokens, 0) ?? 0) * 0.1
    : 0;
  const cacheCreationCost = model
    ? (estimateCostUsd(provider, model, cacheCreationTokens, 0) ?? 0) * 1.25
    : 0;
  const cost = baseCost === null ? null : baseCost + cacheReadCost + cacheCreationCost;
  const totalTokens = inputTokens + outputTokens + cacheReadTokens + cacheCreationTokens;
  return (
    <div className="flex items-start gap-2 text-[11px] text-gray-600 dark:text-gray-400 bg-white dark:bg-gray-950 border border-gray-200 dark:border-gray-800 rounded-lg px-3 py-2">
      <DollarSign className="w-3.5 h-3.5 shrink-0 mt-0.5 text-gray-400" />
      <div className="leading-snug">
        <div>
          Session cost: <strong>{formatCostUsd(cost)}</strong>
          {model && (
            <span className="text-gray-400"> · {provider}/{model}</span>
          )}
        </div>
        <div className="text-gray-400">
          {totalTokens.toLocaleString()} tokens across {turns}{' '}
          {turns === 1 ? 'turn' : 'turns'} ({inputTokens.toLocaleString()} in,{' '}
          {outputTokens.toLocaleString()} out
          {cacheReadTokens > 0 && `, ${cacheReadTokens.toLocaleString()} cached`}
          )
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ role, content }: { role: 'user' | 'assistant'; content: string }) {
  const isUser = role === 'user';
  // Strip <state> and <draft> JSON blocks from assistant messages — they're
  // for the server, not the user. Keeps the right-pane preview as the single
  // source of truth for "what's in the draft".
  const display = isUser ? content : stripBlocks(content);
  return (
    <div className={`flex gap-2 ${isUser ? 'flex-row-reverse' : ''}`}>
      <div
        className={`shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-white text-xs ${
          isUser ? 'bg-blue-600' : 'bg-gray-700 dark:bg-gray-600'
        }`}
        aria-hidden
      >
        {isUser ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
      </div>
      <div
        className={`min-w-0 max-w-[85%] rounded-2xl px-3 py-2 text-sm ${
          isUser
            ? 'bg-blue-600 text-white rounded-tr-sm'
            : 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded-tl-sm'
        }`}
      >
        {isUser ? (
          <span className="whitespace-pre-wrap wrap-break-word">{content || ' '}</span>
        ) : (
          <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-2 prose-pre:my-2 prose-pre:text-[11px] prose-pre:leading-snug prose-pre:bg-gray-50 dark:prose-pre:bg-gray-900 prose-code:text-[12px] prose-headings:my-2 prose-headings:text-base">
            {display ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{display}</ReactMarkdown>
            ) : (
              <Loader2 className="w-3.5 h-3.5 animate-spin text-gray-400" />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
