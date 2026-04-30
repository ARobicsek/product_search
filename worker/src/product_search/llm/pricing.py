"""Best-effort price table for LLM cost estimation.

Used by the worker's run-cost panel and (mirrored in
``web/lib/llm-prices.ts``) the onboarding session-cost indicator. Prices
are public-list rates per million tokens in USD as of January 2026 —
update both files together when providers change rates. Unpriced
``(provider, model)`` pairs return ``None`` so the caller can render an
"(unpriced)" placeholder rather than silently zero the cost.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Pricing:
    input_per_mtok_usd: float
    output_per_mtok_usd: float


# Keep in sync with `web/lib/llm-prices.ts`.
PRICING: dict[tuple[str, str], Pricing] = {
    # Anthropic — public list pricing
    ("anthropic", "claude-opus-4-7"): Pricing(15.0, 75.0),
    ("anthropic", "claude-sonnet-4-6"): Pricing(3.0, 15.0),
    ("anthropic", "claude-haiku-4-5"): Pricing(1.0, 5.0),
    ("anthropic", "claude-haiku-4-5-20251001"): Pricing(1.0, 5.0),
    # Z.AI GLM — paid-tier estimates; the free quota covers most usage of
    # glm-4.5-flash so the actual billed amount may be lower than this
    # estimate. Better to overstate than understate.
    ("glm", "glm-4.5-flash"): Pricing(0.05, 0.05),
    ("glm", "glm-4.6"): Pricing(0.6, 2.2),
    ("glm", "glm-5.1"): Pricing(2.0, 8.0),
    # OpenAI — cheap tier
    ("openai", "gpt-4o-mini"): Pricing(0.15, 0.60),
    # Gemini — placeholder; update when actually used
    ("gemini", "gemini-2.0-flash"): Pricing(0.10, 0.40),
}


def estimate_cost_usd(
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """Return the dollar cost for one call, or ``None`` if pricing is unknown.

    Tokens of ``None`` are treated as zero. A ``(provider, model)`` not in
    :data:`PRICING` returns ``None`` so the caller can show "(unpriced)"
    instead of misleadingly displaying $0.0000.
    """
    pricing = PRICING.get((provider, model))
    if pricing is None:
        return None
    in_tok = input_tokens or 0
    out_tok = output_tokens or 0
    return (
        in_tok * pricing.input_per_mtok_usd / 1_000_000
        + out_tok * pricing.output_per_mtok_usd / 1_000_000
    )


def format_cost_usd(cost: float | None) -> str:
    """Format a cost for display: ``"$0.0042"`` or ``"(unpriced)"`` for None."""
    if cost is None:
        return "(unpriced)"
    if cost < 0.0001:
        return "<$0.0001"
    return f"${cost:.4f}"
