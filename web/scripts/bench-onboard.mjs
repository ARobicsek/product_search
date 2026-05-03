// Phase 14 bench: drive a scripted 15-turn onboarding dialogue against the
// new Anthropic Haiku 4.5 + web_search + prompt-caching pipeline. Mirrors
// the live chat route's compression logic so the numbers are honest.
//
// Run: node --env-file=../.env scripts/bench-onboard.js
//
// Outputs per-turn token usage, cumulative cost, and a final summary that
// can be diffed against the GLM-5.1 baseline ($/M token rates differ — the
// table in lib/llm-prices.ts is the source of truth).
//
// Cost note: this script calls the real Anthropic API. Expect spend in the
// $0.05–$0.20 range depending on how many web searches the model fires.

import Anthropic from '@anthropic-ai/sdk';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const promptText = fs
  .readFileSync(
    path.join(__dirname, '../../worker/src/product_search/onboarding/prompts/onboard_v1.txt'),
    'utf8',
  )
  .replace(/\n\s*\n/g, '\n')
  .trim();

const MODEL = 'claude-haiku-4-5';
const MAX_TOKENS = 4096;
const KEEP_HEAD = 1;
const KEEP_TAIL = 4;

// Pricing table (USD per million tokens). Cache reads cost 0.1× input;
// cache creations cost 1.25× input. Mirrors lib/llm-prices.ts.
const PRICING = {
  'claude-haiku-4-5': { input: 1.0, output: 5.0 },
  'glm-5.1': { input: 2.0, output: 8.0 }, // baseline reference
};

// Scripted user dialogue. 15 turns of a realistic onboarding for "noise-
// cancelling headphones budget under $300". Includes a vendor-discovery
// turn that should trigger a web search.
const USER_TURNS = [
  "Hi — I'd like to onboard a new product to track. Please ask me your first question.",
  "I'm shopping for noise-cancelling over-ear headphones, budget under $300.",
  "Sony WH-1000XM5, Bose QuietComfort Ultra, and Sennheiser Momentum 4 — those are the three I'm comparing.",
  "Hard requirement: must be active noise-cancelling, not just passive. And new or refurbished, not used.",
  "Soft flags: I'd like to see condition and brand on the report. Avoid sellers shipping from CN/HK if possible.",
  "Default eBay queries are fine. For other vendors, what do you suggest beyond eBay for these specific models?",
  "Yes, please add Crutchfield and B&H if they carry these.",
  "Skip that one — let's just stick with eBay, Crutchfield, and B&H.",
  "No QVL or compatibility list — these are consumer headphones.",
  "One synthesis hint: refurbished from authorised resellers is usually 30-40% cheaper than new and worth flagging.",
  "Default columns are fine, but please add 'condition' and 'brand', and drop 'qty' since it's meaningless for consumer audio.",
  "Daily at 08:00 UTC is fine.",
  "Wait — what slug did we agree on?",
  "Good. And what's the display_name?",
  "Perfect, looks good — I'll save it from the UI.",
];

function findLatestStateRaw(messages) {
  const re = /<state>([\s\S]*?)<\/state>/i;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== 'assistant') continue;
    const m = re.exec(messages[i].content);
    if (m) return m[1].trim();
  }
  return null;
}

function compressWithLedger(messages) {
  if (messages.length <= KEEP_HEAD + KEEP_TAIL) return messages;
  const head = messages.slice(0, KEEP_HEAD);
  const tail = messages.slice(messages.length - KEEP_TAIL);
  const middle = messages.slice(KEEP_HEAD, messages.length - KEEP_TAIL);
  const ledgerRaw = findLatestStateRaw(middle) ?? findLatestStateRaw(tail);
  if (!ledgerRaw) return [...head, ...tail];
  return [
    ...head,
    {
      role: 'assistant',
      content: `[Earlier turns elided. Decisions confirmed so far:]\n<state>${ledgerRaw}</state>`,
    },
    ...tail,
  ];
}

function extractDraftJson(text) {
  const m = /<draft>([\s\S]*?)<\/draft>/i.exec(text);
  if (!m) return null;
  try {
    return JSON.parse(m[1].trim());
  } catch {
    return null;
  }
}

function costForTurn(usage) {
  const p = PRICING[MODEL];
  const input = ((usage.input_tokens || 0) * p.input) / 1e6;
  const output = ((usage.output_tokens || 0) * p.output) / 1e6;
  const cacheRead = ((usage.cache_read_input_tokens || 0) * p.input * 0.1) / 1e6;
  const cacheCreate = ((usage.cache_creation_input_tokens || 0) * p.input * 1.25) / 1e6;
  return { input, output, cacheRead, cacheCreate, total: input + output + cacheRead + cacheCreate };
}

async function main() {
  if (!process.env.ANTHROPIC_API_KEY) {
    console.error('ANTHROPIC_API_KEY not set. Run with: node --env-file=../.env scripts/bench-onboard.js');
    process.exit(1);
  }
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

  const messages = [];
  let totalInput = 0, totalOutput = 0, totalCacheRead = 0, totalCacheCreate = 0;
  let totalCost = 0, totalSearches = 0;
  let lastSlug = null, lastDisplayName = null;

  for (let turn = 0; turn < USER_TURNS.length; turn++) {
    messages.push({ role: 'user', content: USER_TURNS[turn] });
    const trimmed = compressWithLedger(messages);

    const startMs = Date.now();
    const response = await client.messages.create({
      model: MODEL,
      max_tokens: MAX_TOKENS,
      system: [{ type: 'text', text: promptText, cache_control: { type: 'ephemeral' } }],
      tools: [{ type: 'web_search_20250305', name: 'web_search', max_uses: 5 }],
      messages: trimmed,
    });
    const elapsedMs = Date.now() - startMs;

    let assistantText = '';
    for (const block of response.content) {
      if (block.type === 'text') assistantText += block.text;
    }
    messages.push({ role: 'assistant', content: assistantText });

    const u = response.usage;
    const c = costForTurn(u);
    totalInput += u.input_tokens || 0;
    totalOutput += u.output_tokens || 0;
    totalCacheRead += u.cache_read_input_tokens || 0;
    totalCacheCreate += u.cache_creation_input_tokens || 0;
    totalCost += c.total;
    const searches = u.server_tool_use?.web_search_requests || 0;
    totalSearches += searches;

    const draft = extractDraftJson(assistantText);
    if (draft) {
      if (draft.slug && typeof draft.slug === 'string') lastSlug = draft.slug;
      if (draft.display_name && typeof draft.display_name === 'string') lastDisplayName = draft.display_name;
    }

    console.log(
      `[t${String(turn + 1).padStart(2)}] in=${u.input_tokens} out=${u.output_tokens} ` +
      `cache_r=${u.cache_read_input_tokens || 0} cache_c=${u.cache_creation_input_tokens || 0} ` +
      `searches=${searches} ms=${elapsedMs} cost=$${c.total.toFixed(5)} ` +
      `slug=${lastSlug ?? '-'}`
    );
  }

  console.log('\n=== Phase 14 bench summary ===');
  console.log(`Model: ${MODEL}`);
  console.log(`Turns: ${USER_TURNS.length}`);
  console.log(`Tokens — input=${totalInput.toLocaleString()} output=${totalOutput.toLocaleString()} cache_r=${totalCacheRead.toLocaleString()} cache_c=${totalCacheCreate.toLocaleString()}`);
  console.log(`Web searches: ${totalSearches}`);
  console.log(`Total cost: $${totalCost.toFixed(4)}`);
  console.log(`Final slug: ${lastSlug ?? '(none)'}`);
  console.log(`Final display_name: ${lastDisplayName ?? '(none)'}`);

  // Compare vs GLM-5.1 baseline. Continuation 11 logged GLM-5.1 onboarding
  // sessions costing roughly $0.04–$0.08 (w/ web_search). 30% of $0.06 ≈
  // $0.018. Phase 14's done-when says ≤30% of GLM-5.1 baseline.
  const glmBaselineMin = 0.04;
  const glmBaselineMax = 0.08;
  const targetMax = glmBaselineMax * 0.30;
  console.log(`\nGLM-5.1 baseline (continuation 11): $${glmBaselineMin.toFixed(4)}–$${glmBaselineMax.toFixed(4)}/session`);
  console.log(`Phase 14 target: ≤$${targetMax.toFixed(4)}/session (30% of GLM upper bound)`);
  console.log(`Phase 14 actual: $${totalCost.toFixed(4)} ${totalCost <= targetMax ? '✓' : '✗'}`);
}

main().catch((err) => {
  console.error('bench failed:', err);
  process.exit(1);
});
