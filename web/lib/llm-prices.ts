// Best-effort LLM price table for cost estimation in the onboarding chat
// session-cost indicator and (mirrored) the worker's run-cost panel.
//
// Prices are public-list rates per million tokens in USD as of January 2026.
// Update both this file AND `worker/src/product_search/llm/pricing.py` when
// a provider changes rates. Unknown (provider, model) pairs return null so
// the caller can render "(unpriced)" rather than misleadingly displaying $0.

export interface ProviderPricing {
  inputPerMTokUsd: number;
  outputPerMTokUsd: number;
}

// Keyed as `${provider}/${model}` (matches the Python tuple keys).
export const LLM_PRICING: Record<string, ProviderPricing> = {
  // Anthropic — public list pricing
  'anthropic/claude-opus-4-7': { inputPerMTokUsd: 15.0, outputPerMTokUsd: 75.0 },
  'anthropic/claude-sonnet-4-6': { inputPerMTokUsd: 3.0, outputPerMTokUsd: 15.0 },
  'anthropic/claude-haiku-4-5': { inputPerMTokUsd: 1.0, outputPerMTokUsd: 5.0 },
  'anthropic/claude-haiku-4-5-20251001': { inputPerMTokUsd: 1.0, outputPerMTokUsd: 5.0 },
  // Z.AI GLM — paid-tier estimates; free quota covers most usage of glm-4.5-flash
  'glm/glm-4.5-flash': { inputPerMTokUsd: 0.05, outputPerMTokUsd: 0.05 },
  'glm/glm-4.6': { inputPerMTokUsd: 0.6, outputPerMTokUsd: 2.2 },
  'glm/glm-5.1': { inputPerMTokUsd: 2.0, outputPerMTokUsd: 8.0 },
  // OpenAI — cheap tier
  'openai/gpt-4o-mini': { inputPerMTokUsd: 0.15, outputPerMTokUsd: 0.6 },
  // Gemini — placeholder; update when actually used
  'gemini/gemini-2.0-flash': { inputPerMTokUsd: 0.1, outputPerMTokUsd: 0.4 },
};

export function estimateCostUsd(
  provider: string,
  model: string,
  inputTokens: number | null | undefined,
  outputTokens: number | null | undefined
): number | null {
  const pricing = LLM_PRICING[`${provider}/${model}`];
  if (!pricing) return null;
  const inTok = inputTokens ?? 0;
  const outTok = outputTokens ?? 0;
  return (
    (inTok * pricing.inputPerMTokUsd) / 1_000_000 +
    (outTok * pricing.outputPerMTokUsd) / 1_000_000
  );
}

export function formatCostUsd(cost: number | null): string {
  if (cost === null) return '(unpriced)';
  if (cost < 0.0001) return '<$0.0001';
  return `$${cost.toFixed(4)}`;
}
