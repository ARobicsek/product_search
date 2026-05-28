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
  Loader2,
  Save,
  Search,
  Send,
  Trash2,
  User,
  X,
} from 'lucide-react';
import { estimateCostUsd, formatCostUsd } from '@/lib/llm-prices';
import { extractDraftJson, extractStateJson, stripBlocks } from '@/lib/onboard/blocks';
import { renderProfileYaml } from '@/lib/onboard/render-yaml';

interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'success'; slug: string; commitUrl: string | null; probeStatus: string; warnings: string[] }
  | { kind: 'error'; message: string; details?: string[] };

// ADR-115: save-time probe modal state.
type UrlProbeStatus = 'queued' | 'probing' | 'ok' | 'failed' | 'deadline';

interface UrlProbeRow {
  url: string;
  host: string | null;
  pageType: 'search' | 'detail';
  status: UrlProbeStatus;
  reason?: string;
}

interface BackfillRow {
  host: string;
  state: 'running' | 'added' | 'failed' | 'skipped';
  detail?: string;
  urls: string[];
}

type ProbeState =
  | { kind: 'idle' }
  | {
      kind: 'streaming';
      attempt: number;
      phase: string;
      rows: UrlProbeRow[];
      backfills: BackfillRow[];
    }
  | {
      kind: 'awaiting';
      attempt: number;
      rows: UrlProbeRow[];
      backfills: BackfillRow[];
      enrichedDraft: Record<string, unknown>;
      unprobed: string[];
      validationErrors: string[];
      validationWarnings: string[];
      // If the only validation errors are the ADR-111 force_detail_backup
      // ones, "Save and proceed anyway" is offered as a bypass. Other errors
      // (schema, missing match_aliases, etc.) genuinely block save.
      bypassableViolations: boolean;
    }
  | { kind: 'error'; message: string };

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
  // ADR-114: drafts streamed from the server (extracted from <draft> blocks in
  // each iteration's assistant text, plus the input to validate_profile tool
  // calls). The server can see drafts the client never will — Anthropic stops
  // each message at the first tool_use, so during long probe loops the client
  // never receives a closing </draft> and falls back to turn-1's empty stub.
  const [latestToolDraft, setLatestToolDraft] = useState<Record<string, unknown> | null>(null);
  // ADR-115: save-time probe modal state.
  const [probeState, setProbeState] = useState<ProbeState>({ kind: 'idle' });
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
          if (payload.name === 'probe_url') {
            let hostStr = 'URL';
            try {
              if (payload.input?.url) {
                hostStr = new URL(payload.input.url as string).host;
              }
            } catch {
              hostStr = 'URL';
            }
            setStatusLine(`Probing ${hostStr}…`);
          } else {
            setStatusLine('Searching the web…');
          }
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
        } else if (payload.type === 'status' && typeof payload.message === 'string') {
          setStatusLine(payload.message);
        } else if (payload.type === 'draft_update' && payload.draft && typeof payload.draft === 'object') {
          // ADR-114: server-streamed draft. Always replace — the server emits
          // these in order (per validate_profile call and per <draft> block
          // it parses out of an iteration's assistant text), so the latest
          // event is the freshest known draft.
          setLatestToolDraft(payload.draft);
        } else if (payload.type === 'error') {
          setError(payload.error ?? 'unknown error');
        } else if (payload.type === 'done') {
          // The model hit the per-message output cap before finishing. Without
          // this hint the assistant message just ends mid-thought and the user
          // sees a frozen UI — that's the failure mode that prompted bumping
          // MAX_TOKENS in route.ts. Tell the user they can resume.
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
    // conversation — typically to address a warning or add coverage. Clear the
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

  // ADR-114: prefer the server-streamed draft over the client-parsed <draft>
  // block. The server can see drafts that never reach the client closing tag
  // (each Anthropic message ends at the first tool_use, so multi-tool turns
  // never emit a complete </draft>). Fall back to the message-text scanner
  // when no server stream has arrived yet, and to the initial YAML in edit
  // mode if neither source has populated.
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

  // ADR-115: actually POST to /api/onboard/save with whatever draft + flags
  // the caller hands us. Used by both the auto-save-after-clean-probe path
  // and the "Save and proceed anyway" bypass path.
  async function commitSave(
    draft: Record<string, unknown>,
    bypassForceDetailBackup: boolean,
  ): Promise<void> {
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
          bypassForceDetailBackup,
        }),
      });
      const data = (await res.json()) as {
        ok: boolean;
        slug?: string;
        commitUrl?: string | null;
        error?: string;
        details?: string[];
        probeStatus?: string;
        warnings?: Array<{ host: string; message: string }>;
      };
      if (!res.ok || !data.ok || !data.slug) {
        if (res.status === 422) {
          const errorMsg = data.error ?? 'Validation failed';
          const detailsList = data.details && data.details.length > 0 ? `\n- ${data.details.join('\n- ')}` : '';
          const prompt = `I clicked Save but the profile failed validation:\n${errorMsg}${detailsList}\n\nPlease fix these errors and re-validate before asking me to save again.`;
          setSaveState({ kind: 'idle' });
          setProbeState({ kind: 'idle' });
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
      const warnings = (data.warnings ?? []).map((w) => w.message);
      setSaveState({
        kind: 'success',
        slug: data.slug,
        commitUrl: data.commitUrl ?? null,
        probeStatus: data.probeStatus ?? 'skipped',
        warnings,
      });
      setProbeState({ kind: 'idle' });
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

  // ADR-115: stream a probe pass against /api/onboard/probe. Reuses the same
  // SSE shape pattern as /api/onboard/chat. Caller supplies the draft to
  // probe; carryOver lets "Continue probing" resume from a prior partial pass
  // without re-probing already-finished URLs.
  async function streamProbe(
    draft: Record<string, unknown>,
    attempt: number,
    carryOver?: {
      unprobed: string[];
      priorResults: Array<{ url: string; ok: boolean; reason: string | null }>;
      rows: UrlProbeRow[];
      backfills: BackfillRow[];
    },
  ): Promise<void> {
    setProbeState({
      kind: 'streaming',
      attempt,
      phase: carryOver ? 'Resuming probe…' : 'Starting probe…',
      rows: carryOver?.rows ?? [],
      backfills: carryOver?.backfills ?? [],
    });
    const secret = process.env.NEXT_PUBLIC_WEB_SHARED_SECRET ?? '';
    let response: Response;
    try {
      response = await fetch('/api/onboard/probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-web-secret': secret },
        body: JSON.stringify({
          draft,
          unprobed: carryOver?.unprobed,
          priorResults: carryOver?.priorResults,
        }),
      });
    } catch (err) {
      setProbeState({
        kind: 'error',
        message: err instanceof Error ? err.message : 'probe request failed',
      });
      return;
    }
    if (!response.ok || !response.body) {
      const txt = await response.text().catch(() => '');
      setProbeState({
        kind: 'error',
        message: `probe request failed: ${response.status} ${txt.slice(0, 200)}`,
      });
      return;
    }

    // Mutable copies we'll re-assign into state as events arrive.
    const rows: UrlProbeRow[] = [...(carryOver?.rows ?? [])];
    const backfills: BackfillRow[] = [...(carryOver?.backfills ?? [])];
    let phase = 'Probing…';

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const updateRow = (url: string, patch: Partial<UrlProbeRow>) => {
      const idx = rows.findIndex((r) => r.url === url);
      if (idx >= 0) rows[idx] = { ...rows[idx], ...patch };
      else rows.push({ url, host: null, pageType: 'search', status: 'queued', ...patch });
    };

    const flush = () => {
      setProbeState({
        kind: 'streaming',
        attempt,
        phase,
        rows: [...rows],
        backfills: [...backfills],
      });
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 2);
        if (!raw.startsWith('data:')) continue;
        const json = raw.slice(5).trim();
        if (!json) continue;
        let p: Record<string, unknown>;
        try { p = JSON.parse(json) as Record<string, unknown>; } catch { continue; }
        const t = p.type as string | undefined;
        if (t === 'phase' && typeof p.message === 'string') {
          phase = p.message;
        } else if (t === 'url_start' && typeof p.url === 'string') {
          updateRow(p.url, {
            host: (p.host as string | null) ?? null,
            pageType: (p.pageType as 'search' | 'detail') ?? 'search',
            status: 'probing',
          });
        } else if (t === 'url_done' && typeof p.url === 'string') {
          updateRow(p.url, {
            host: (p.host as string | null) ?? null,
            status: p.ok ? 'ok' : 'failed',
            reason: typeof p.reason === 'string' ? p.reason : undefined,
          });
        } else if (t === 'url_deadline' && typeof p.url === 'string') {
          updateRow(p.url, {
            status: 'deadline',
            reason: 'budget exhausted before this URL was probed',
          });
        } else if (t === 'backfill_start' && typeof p.host === 'string') {
          backfills.push({ host: p.host, state: 'running', urls: [] });
        } else if (t === 'backfill_added' && typeof p.host === 'string' && typeof p.url === 'string') {
          const last = backfills[backfills.length - 1];
          if (last && last.host === p.host) last.urls.push(p.url);
        } else if (t === 'backfill_failed' && typeof p.host === 'string') {
          // Recorded as the host's per-row event; the backfill is not marked
          // failed unless every candidate failed (see backfill_done below).
        } else if (t === 'backfill_skip' && typeof p.host === 'string') {
          backfills.push({
            host: p.host,
            state: 'skipped',
            detail: typeof p.reason === 'string' ? p.reason : undefined,
            urls: [],
          });
        } else if (t === 'backfill_done' && typeof p.host === 'string') {
          const last = backfills[backfills.length - 1];
          if (last && last.host === p.host) {
            last.state = (p.added as number) > 0 ? 'added' : 'failed';
          }
        } else if (t === 'done') {
          const enrichedDraft = p.enrichedDraft as Record<string, unknown>;
          const complete = p.complete === true;
          const unprobed = Array.isArray(p.unprobed) ? (p.unprobed as string[]) : [];
          const validation = (p.validation as {
            ok: boolean;
            errors: string[];
            warnings: string[];
          } | undefined) ?? { ok: true, errors: [], warnings: [] };

          if (complete && validation.ok) {
            // All clean. Drop the modal and commit silently.
            await commitSave(enrichedDraft, false);
            return;
          }
          const bypassableViolations = validation.errors.length > 0 && validation.errors.every((e) =>
            /save is BLOCKED|Save is BLOCKED|ADR-067|ADR-111|force_detail_backup/.test(e)
          );
          setProbeState({
            kind: 'awaiting',
            attempt,
            rows,
            backfills,
            enrichedDraft,
            unprobed,
            validationErrors: validation.errors,
            validationWarnings: validation.warnings,
            bypassableViolations: bypassableViolations || (complete && !validation.ok && bypassableViolations),
          });
          return;
        } else if (t === 'error') {
          setProbeState({
            kind: 'error',
            message: typeof p.error === 'string' ? p.error : 'probe stream error',
          });
          return;
        }
        flush();
      }
    }
    // If we exited without a `done` event, surface that as an error.
    setProbeState({ kind: 'error', message: 'probe stream ended without a done event' });
  }

  function onSave() {
    if (!draftIntent || saveState.kind === 'saving' || probeState.kind === 'streaming') return;
    void streamProbe(draftIntent, 1);
  }

  function onContinueProbe() {
    if (probeState.kind !== 'awaiting') return;
    const carryOver = {
      unprobed: probeState.unprobed,
      priorResults: probeState.rows
        .filter((r) => r.status === 'ok' || r.status === 'failed')
        .map((r) => ({ url: r.url, ok: r.status === 'ok', reason: r.reason ?? null })),
      rows: probeState.rows,
      backfills: probeState.backfills,
    };
    void streamProbe(probeState.enrichedDraft, probeState.attempt + 1, carryOver);
  }

  function onSaveAnyway() {
    if (probeState.kind !== 'awaiting') return;
    void commitSave(probeState.enrichedDraft, probeState.bypassableViolations);
  }

  function onCancelProbe() {
    setProbeState({ kind: 'idle' });
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
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-[11px] text-green-700 dark:text-green-400 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-900/40 rounded-lg px-3 py-2">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                <span>
                  Saved <strong>{saveState.slug}</strong>.
                  {saveState.probeStatus === 'pending'
                    ? ' URL validation running in background…'
                    : saveState.warnings.length === 0
                      ? ' Opening the page…'
                      : ''}
                </span>
              </div>
              {saveState.warnings.length > 0 && (
                <div className="text-[11px] text-amber-800 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-900/40 rounded-lg px-3 py-2 space-y-1">
                  <div className="font-medium">Heads up — coverage could be better:</div>
                  <ul className="list-disc ml-4 space-y-0.5">
                    {saveState.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                  <a
                    href={`/${saveState.slug}`}
                    className="inline-block mt-1 underline hover:no-underline"
                  >
                    Open {saveState.slug} anyway →
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
              streaming ||
              probeState.kind === 'streaming'
            }
            className="w-full flex items-center justify-center gap-2 rounded-lg bg-blue-600 text-white text-sm font-medium px-4 py-2 disabled:bg-gray-300 disabled:dark:bg-gray-700 disabled:cursor-not-allowed hover:bg-blue-700 transition focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {saveState.kind === 'saving' || probeState.kind === 'streaming' ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Save className="w-4 h-4" />
            )}
            {saveState.kind === 'saving'
              ? 'Committing…'
              : probeState.kind === 'streaming'
                ? 'Probing vendor URLs…'
                : 'Save profile to repo'}
          </button>
        </div>
      </aside>

      {probeState.kind !== 'idle' && (
        <ProbeModal
          state={probeState}
          onContinue={onContinueProbe}
          onSaveAnyway={onSaveAnyway}
          onCancel={onCancelProbe}
        />
      )}
    </div>
  );
}

function ProbeModal({
  state,
  onContinue,
  onSaveAnyway,
  onCancel,
}: {
  state: ProbeState;
  onContinue: () => void;
  onSaveAnyway: () => void;
  onCancel: () => void;
}) {
  if (state.kind === 'idle') return null;

  const isStreaming = state.kind === 'streaming';
  const isAwaiting = state.kind === 'awaiting';
  const isError = state.kind === 'error';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-3"
      role="dialog"
      aria-modal="true"
    >
      <div className="bg-white dark:bg-gray-950 rounded-xl border border-gray-200 dark:border-gray-800 w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden shadow-2xl">
        <header className="px-4 py-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            {isStreaming && <Loader2 className="w-4 h-4 animate-spin text-blue-600" />}
            {isAwaiting && <AlertCircle className="w-4 h-4 text-amber-600" />}
            {isError && <AlertCircle className="w-4 h-4 text-red-600" />}
            <h3 className="text-sm font-semibold">
              {isStreaming && `Save-time probe (attempt ${state.attempt})`}
              {isAwaiting && `Probe attempt ${state.attempt} — choose how to proceed`}
              {isError && 'Probe failed'}
            </h3>
          </div>
          {!isStreaming && (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-full p-1 hover:bg-gray-100 dark:hover:bg-gray-800"
              aria-label="Close"
            >
              <X className="w-4 h-4 text-gray-500" />
            </button>
          )}
        </header>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {isStreaming && (
            <p className="text-xs text-gray-600 dark:text-gray-400">{state.phase}</p>
          )}

          {(isStreaming || isAwaiting) && state.rows.length > 0 && (
            <ul className="space-y-1.5">
              {state.rows.map((row) => (
                <li
                  key={row.url}
                  className="flex items-start gap-2 text-xs border border-gray-200 dark:border-gray-800 rounded-lg px-2.5 py-1.5"
                >
                  <span className="mt-0.5 w-3 h-3 shrink-0 flex items-center justify-center">
                    {row.status === 'probing' && <Loader2 className="w-3 h-3 animate-spin text-blue-600" />}
                    {row.status === 'ok' && <CheckCircle2 className="w-3 h-3 text-emerald-600" />}
                    {row.status === 'failed' && <AlertCircle className="w-3 h-3 text-red-600" />}
                    {row.status === 'deadline' && <AlertCircle className="w-3 h-3 text-amber-600" />}
                    {row.status === 'queued' && <span className="w-2 h-2 rounded-full bg-gray-300 dark:bg-gray-700" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium truncate">{row.host ?? row.url}</span>
                      {row.pageType === 'detail' && (
                        <span className="text-[10px] px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
                          detail
                        </span>
                      )}
                    </div>
                    <div className="text-[11px] text-gray-500 dark:text-gray-400 truncate">{row.url}</div>
                    {row.reason && (
                      <div className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5">{row.reason}</div>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}

          {(isStreaming || isAwaiting) && state.backfills.length > 0 && (
            <div className="border-t border-gray-200 dark:border-gray-800 pt-3 space-y-1.5">
              <div className="text-[11px] uppercase tracking-wide text-gray-500 font-medium">
                Detail-URL backfill
              </div>
              {state.backfills.map((b, i) => (
                <div
                  key={`${b.host}-${i}`}
                  className="flex items-start gap-2 text-xs border border-gray-200 dark:border-gray-800 rounded-lg px-2.5 py-1.5"
                >
                  <span className="mt-0.5 w-3 h-3 shrink-0 flex items-center justify-center">
                    {b.state === 'running' && <Loader2 className="w-3 h-3 animate-spin text-blue-600" />}
                    {b.state === 'added' && <CheckCircle2 className="w-3 h-3 text-emerald-600" />}
                    {b.state === 'failed' && <AlertCircle className="w-3 h-3 text-red-600" />}
                    {b.state === 'skipped' && <AlertCircle className="w-3 h-3 text-gray-400" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium">{b.host}</div>
                    {b.detail && (
                      <div className="text-[11px] text-gray-500 dark:text-gray-400">{b.detail}</div>
                    )}
                    {b.urls.length > 0 && (
                      <ul className="text-[11px] text-gray-500 dark:text-gray-400 truncate">
                        {b.urls.map((u) => (
                          <li key={u} className="truncate">+ {u}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {isAwaiting && state.validationErrors.length > 0 && (
            <div className="border border-amber-200 dark:border-amber-900/40 bg-amber-50 dark:bg-amber-900/20 rounded-lg px-3 py-2 text-[11px] text-amber-800 dark:text-amber-300 space-y-1">
              <div className="font-medium">
                {state.bypassableViolations
                  ? 'These would block save under the ADR-111 detail-URL gate:'
                  : 'These errors will block save:'}
              </div>
              <ul className="list-disc ml-4 space-y-0.5">
                {state.validationErrors.slice(0, 6).map((e, i) => (
                  <li key={i} className="break-words">{e}</li>
                ))}
              </ul>
            </div>
          )}

          {isAwaiting && state.unprobed.length > 0 && (
            <div className="text-[11px] text-gray-500 dark:text-gray-400">
              {state.unprobed.length} URL(s) didn&apos;t finish probing within the budget.
            </div>
          )}

          {isError && (
            <div className="text-xs text-red-700 dark:text-red-400 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-900/40 rounded-lg px-3 py-2">
              {state.message}
            </div>
          )}
        </div>

        {isAwaiting && (
          <footer className="border-t border-gray-200 dark:border-gray-800 p-3 flex flex-wrap gap-2 justify-end">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg border border-gray-300 dark:border-gray-700 px-3 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-900"
            >
              Cancel
            </button>
            {state.unprobed.length > 0 && (
              <button
                type="button"
                onClick={onContinue}
                className="rounded-lg bg-blue-600 text-white px-3 py-1.5 text-xs hover:bg-blue-700"
              >
                Continue probing
              </button>
            )}
            {(state.bypassableViolations || state.validationErrors.length === 0) && (
              <button
                type="button"
                onClick={onSaveAnyway}
                className="rounded-lg bg-amber-600 text-white px-3 py-1.5 text-xs hover:bg-amber-700"
              >
                {state.validationErrors.length > 0
                  ? 'Save and proceed anyway'
                  : 'Save now'}
              </button>
            )}
          </footer>
        )}

        {isError && (
          <footer className="border-t border-gray-200 dark:border-gray-800 p-3 flex justify-end">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-lg border border-gray-300 dark:border-gray-700 px-3 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-900"
            >
              Close
            </button>
          </footer>
        )}
      </div>
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
