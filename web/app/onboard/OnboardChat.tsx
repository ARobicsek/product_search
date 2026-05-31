'use client';

import { FormEvent, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import yaml from 'js-yaml';
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  DollarSign,
  ExternalLink,
  Loader2,
  Save,
  Search,
  Send,
  Trash2,
  User,
} from 'lucide-react';
import { estimateCostUsd, formatCostUsd } from '@/lib/llm-prices';
import { extractDraftJson, extractStateJson, stripBlocks } from '@/lib/onboard/blocks';
import { renderProfileV2Yaml } from '@/lib/onboard/validation-v2';
import type { SerperItem } from '@/lib/serper';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'success'; slug: string; commitUrl: string | null; probeStatus: string; warnings: string[] }
  | { kind: 'error'; message: string; details?: string[] };

// Phase 34 (ADR-137): the live Serper preview replaces the entire v1 probe
// apparatus. The model fires one real Google Shopping query mid-interview; the
// results stream here so the user and the model confirm the query surfaces the
// item together before saving.
interface SerperPreviewState {
  query: string;
  ok: boolean;
  count: number;
  items: SerperItem[];
  error?: string;
}

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

function findLatestState(messages: ChatMessage[]): Record<string, unknown> | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== 'assistant') continue;
    const j = extractStateJson(messages[i].content);
    if (j) return j;
  }
  return null;
}

function safeRender(intent: Record<string, unknown> | null): string | null {
  if (!intent) return null;
  try {
    return renderProfileV2Yaml(intent);
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
  // ADR-114: drafts streamed from the server (extracted from <draft> blocks in
  // each iteration's assistant text, plus the input to validate_profile tool
  // calls). The server can see drafts the client never will — Anthropic stops
  // each message at the first tool_use, so during multi-tool turns the client
  // never receives a closing </draft> and falls back to turn-1's empty stub.
  const [latestToolDraft, setLatestToolDraft] = useState<Record<string, unknown> | null>(null);
  // Phase 34: the latest live Serper preview (one query the model fired to
  // confirm the draft surfaces the item). Persists in the pane until reset.
  const [serperPreview, setSerperPreview] = useState<SerperPreviewState | null>(null);
  // Set when a chat turn force-finalized mid-research (50s budget). Drives a
  // deterministic "continue" affordance instead of relying on the model's prose.
  const [turnTruncated, setTurnTruncated] = useState(false);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runTurn(history: ChatMessage[]) {
    setStreaming(true);
    setError('');
    setStatusLine('');
    setTurnTruncated(false);
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
    let hasUsage = false;

    while (true) {
      const { done, value } = await reader.read();
      if (done || cancelled.current) {
        if (!hasUsage && !cancelled.current && !error) {
          setError("The connection was interrupted before the draft was finalized. Reply 'continue' to resume.");
        }
        break;
      }
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
          name?: string;
          input?: Record<string, unknown>;
          text?: string;
          error?: string;
          message?: string;
          provider?: string;
          model?: string;
          input_tokens?: number;
          output_tokens?: number;
          cache_read_tokens?: number;
          cache_creation_tokens?: number;
          stopReason?: string | null;
          draft?: Record<string, unknown>;
          // serper_preview event fields.
          query?: string;
          ok?: boolean;
          count?: number;
          items?: SerperItem[];
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
          if (payload.name === 'serper_preview') {
            const q = typeof payload.input?.query === 'string' ? (payload.input.query as string) : '';
            setStatusLine(q ? `Previewing Google Shopping for “${q}”…` : 'Previewing Google Shopping results…');
          } else if (payload.name === 'validate_profile') {
            setStatusLine('Validating the draft…');
          } else {
            setStatusLine('Searching the web…');
          }
        } else if (payload.type === 'serper_preview' && typeof payload.query === 'string') {
          setStatusLine('');
          setSerperPreview({
            query: payload.query,
            ok: payload.ok === true,
            count: typeof payload.count === 'number' ? payload.count : (payload.items?.length ?? 0),
            items: Array.isArray(payload.items) ? payload.items : [],
            error: typeof payload.error === 'string' ? payload.error : undefined,
          });
        } else if (payload.type === 'usage') {
          hasUsage = true;
          setSessionUsage((u) => ({
            inputTokens: u.inputTokens + (payload.input_tokens ?? 0),
            outputTokens: u.outputTokens + (payload.output_tokens ?? 0),
            cacheReadTokens: u.cacheReadTokens + (payload.cache_read_tokens ?? 0),
            cacheCreationTokens: u.cacheCreationTokens + (payload.cache_creation_tokens ?? 0),
            turns: u.turns + 1,
            provider: payload.provider ?? u.provider,
            model: payload.model ?? u.model,
          }));
        } else if (payload.type === 'turn_truncated') {
          setTurnTruncated(true);
        } else if (payload.type === 'status' && typeof payload.message === 'string') {
          setStatusLine(payload.message);
        } else if (payload.type === 'draft_update' && payload.draft && typeof payload.draft === 'object') {
          // ADR-114: server-streamed draft. Always replace — the server emits
          // these in order (per validate_profile call and per <draft> block it
          // parses out of an iteration's assistant text), so the latest event
          // is the freshest known draft.
          setLatestToolDraft(payload.draft);
        } else if (payload.type === 'error') {
          setError(payload.error ?? 'unknown error');
        } else if (payload.type === 'done') {
          // The model hit the per-message output cap before finishing. Without
          // this hint the assistant message just ends mid-thought and the user
          // sees a frozen UI. Tell the user they can resume.
          if (payload.stopReason === 'max_tokens') {
            setError(
              'The assistant ran out of output budget mid-response. Reply "continue" to resume.',
            );
          }
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
    // After a previous save (success or error), the user is now continuing the
    // conversation — typically to refine the query or adjust the spec. Clear the
    // terminal save state so the Save button re-enables for the next draft.
    if (saveState.kind === 'success' || saveState.kind === 'error') {
      setSaveState({ kind: 'idle' });
    }
    const next: ChatMessage[] = [...messages, { role: 'user', content: text }];
    await runTurn(next);
  }

  function onReset() {
    if (streaming) return;
    setMessages([kickoffMessage]);
    setSaveState({ kind: 'idle' });
    setError('');
    setLatestToolDraft(null);
    setSerperPreview(null);
    setTurnTruncated(false);
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

  // Deterministic "continue" affordance after a turn force-finalized mid-research.
  function onContinue() {
    if (streaming) return;
    setTurnTruncated(false);
    if (saveState.kind === 'success' || saveState.kind === 'error') {
      setSaveState({ kind: 'idle' });
    }
    const next: ChatMessage[] = [...messages, { role: 'user', content: 'continue' }];
    void runTurn(next);
  }

  // ADR-114: prefer the server-streamed draft over the client-parsed <draft>
  // block (each Anthropic message ends at the first tool_use, so multi-tool
  // turns never emit a complete </draft>). Fall back to the message-text
  // scanner, then to the initial YAML in edit mode.
  let draftIntent: Record<string, unknown> | null = latestToolDraft ?? findLatestDraft(messages);
  if (!draftIntent && initialProfile) {
    try {
      draftIntent = yaml.load(initialProfile) as Record<string, unknown>;
    } catch {
      // ignore
    }
  }
  const draftYaml = safeRender(draftIntent);
  const draftSlug = (draftIntent?.slug as string | undefined) ?? null;

  // Phase 34: single direct save — no probe pass. POST the v2 draft, auto-forward
  // any 422 validation error back to the model (ADR-113), open the page on success.
  async function commitSave(draft: Record<string, unknown>): Promise<void> {
    setSaveState({ kind: 'saving' });
    const secret = process.env.NEXT_PUBLIC_WEB_SHARED_SECRET ?? '';
    try {
      const res = await fetch('/api/onboard/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-web-secret': secret },
        body: JSON.stringify({
          draft,
          originalSlug: initialSlug,
          state: findLatestState(messages),
        }),
      });
      const data = (await res.json()) as {
        ok: boolean;
        slug?: string;
        commitUrl?: string | null;
        error?: string;
        details?: string[];
        probeStatus?: string;
        warnings?: Array<{ host?: string; message: string; userMessage?: string }>;
      };
      if (!res.ok || !data.ok || !data.slug) {
        if (res.status === 422) {
          // ADR-113: hand the validation error back to the model to fix, rather
          // than showing the user a wall of schema text.
          const errorMsg = data.error ?? 'Validation failed';
          const detailsList = data.details && data.details.length > 0 ? `\n- ${data.details.join('\n- ')}` : '';
          const prompt = `I clicked Save but the profile failed validation:\n${errorMsg}${detailsList}\n\nPlease fix these errors and re-validate before asking me to save again.`;
          setSaveState({ kind: 'idle' });
          const next: ChatMessage[] = [...messages, { role: 'user', content: prompt }];
          await runTurn(next);
          return;
        }
        setSaveState({
          kind: 'error',
          message: data.error ?? `Save failed (${res.status})`,
          details: data.details,
        });
        return;
      }
      // ADR-123: prefer the plain-English text; fall back to the technical
      // message for any in-flight server that hasn't been redeployed.
      const warnings = (data.warnings ?? []).map((w) => w.userMessage ?? w.message);
      setSaveState({
        kind: 'success',
        slug: data.slug,
        commitUrl: data.commitUrl ?? null,
        probeStatus: data.probeStatus ?? 'skipped',
        warnings,
      });
      if (warnings.length === 0) {
        setTimeout(() => router.push(`/${data.slug}`), 800);
      }
    } catch (err) {
      setSaveState({
        kind: 'error',
        message: err instanceof Error ? err.message : 'save failed',
      });
    }
  }

  function onSave() {
    if (!draftIntent || saveState.kind === 'saving' || streaming) return;
    void commitSave(draftIntent);
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
          {turnTruncated && !streaming && (
            <div className="text-xs bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-900/40 rounded-lg px-3 py-2 flex items-start gap-2">
              <Loader2 className="w-3.5 h-3.5 mt-0.5 shrink-0 text-amber-600" />
              <div className="flex-1">
                <span className="text-amber-800 dark:text-amber-200">
                  That turn ran long and was cut off before finishing.
                </span>
                <button
                  type="button"
                  onClick={onContinue}
                  className="ml-2 underline font-medium text-amber-900 dark:text-amber-100 hover:no-underline"
                >
                  Continue
                </button>
              </div>
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

      {/* Draft profile preview + live Serper preview */}
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
          {serperPreview && <SerperPreviewPanel preview={serperPreview} />}
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
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-[11px] text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-900/40 rounded-lg px-3 py-2">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>
                  Saved <strong>{saveState.slug}</strong>.
                  {saveState.warnings.length === 0
                    ? ' Opening the page…'
                    : ''}
                </span>
              </div>
              {saveState.warnings.length > 0 && (
                <div className="text-[11px] text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-900/40 rounded-lg px-3 py-2 space-y-2">
                  <div className="font-medium">Heads up:</div>
                  <ul className="list-disc ml-4 space-y-0.5">
                    {saveState.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                  <p className="text-amber-700 dark:text-amber-400">
                    Your profile is saved. You can open it now and fix these later from
                    Edit Profile, or ask the assistant to fix them first.
                  </p>
                  <a
                    href={`/${saveState.slug}`}
                    className="w-full flex items-center justify-center gap-1.5 rounded-lg bg-amber-600 text-white text-xs font-medium px-3 py-2 hover:bg-amber-700 transition no-underline"
                  >
                    Open my product page →
                  </a>
                </div>
              )}
            </div>
          )}
          <button
            type="button"
            onClick={onSave}
            disabled={
              !draftYaml ||
              saveState.kind === 'saving' ||
              saveState.kind === 'success' ||
              streaming
            }
            className="w-full flex items-center justify-center gap-2 rounded-lg bg-blue-600 text-white text-sm font-medium px-4 py-2 disabled:bg-gray-300 disabled:dark:bg-gray-700 disabled:cursor-not-allowed hover:bg-blue-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {saveState.kind === 'saving' ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {saveState.kind === 'saving' ? 'Committing…' : 'Save profile to repo'}
          </button>
        </div>
      </aside>
    </div>
  );
}

// Phase 34: compact live Serper preview panel. Renders the one real Google
// Shopping query the model fired so the user can confirm the target surfaces
// before saving. Honest (ADR-001): every field is verbatim from Serper.
function SerperPreviewPanel({ preview }: { preview: SerperPreviewState }) {
  return (
    <div className="border-t border-gray-200 dark:border-gray-800">
      <div className="px-3 sm:px-4 pt-3 pb-2 flex items-center gap-2">
        <Search className="w-3.5 h-3.5 text-blue-600 shrink-0" />
        <div className="min-w-0">
          <div className="text-xs font-semibold">Live preview · Google Shopping</div>
          <div className="text-[11px] text-gray-500 truncate">“{preview.query}”</div>
        </div>
      </div>
      {!preview.ok ? (
        <div className="mx-3 sm:mx-4 mb-3 flex items-start gap-2 text-[11px] text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-900/40 rounded-lg px-3 py-2">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>Preview couldn&apos;t run: {preview.error ?? 'unknown error'}.</span>
        </div>
      ) : preview.count === 0 ? (
        <div className="mx-3 sm:mx-4 mb-3 flex items-start gap-2 text-[11px] text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-900/40 rounded-lg px-3 py-2">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>No offers found for this query — it may be too specific or mis-scoped.</span>
        </div>
      ) : (
        <>
          <div className="px-3 sm:px-4 pb-1 text-[11px] text-gray-500">
            {preview.count} result{preview.count === 1 ? '' : 's'} (showing top {preview.items.length})
          </div>
          <ul className="px-3 sm:px-4 pb-3 space-y-1.5">
            {preview.items.map((it, i) => (
              <li
                key={it.productId ?? it.link ?? i}
                className="text-xs border border-gray-200 dark:border-gray-800 rounded-lg px-2.5 py-1.5"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="min-w-0 flex-1 font-medium text-gray-800 dark:text-gray-200 line-clamp-2">
                    {it.title}
                  </span>
                  {it.link && (
                    <a
                      href={it.link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="shrink-0 text-gray-400 hover:text-blue-600"
                      aria-label="Open listing"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </a>
                  )}
                </div>
                <div className="mt-0.5 flex items-center gap-2 text-[11px] text-gray-500 dark:text-gray-400">
                  <span className="truncate">{it.merchant ?? 'unknown merchant'}</span>
                  <span className="font-semibold text-gray-700 dark:text-gray-300 ml-auto shrink-0">
                    {it.priceText ?? (it.price != null ? `$${it.price}` : '—')}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
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
