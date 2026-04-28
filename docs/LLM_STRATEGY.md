# LLM Strategy

Goal: cheapest model that passes a fixed test bar at each call site.

## Four call sites, four requirement profiles

| Call site | Volume | Capability needed | Cost sensitivity |
|---|---|---|---|
| **Synthesizer** | 1 call per product per scheduled run + per on-demand. ~30/day across products. | Follow instructions, format JSON into markdown, no fabrication. | High. Cheapest tier preferred. |
| **Onboarding interview** | A handful per week. ~10 turns per onboarding. | Multi-turn conversation, structured YAML output, asks vs. assumes. | Low. Mid tier acceptable. |
| **Onboarding source discovery (Web Search)** | A handful per week during onboarding. | Strong tool-use (web search) capabilities to suggest candidate sources. | Low. Mid/high tier acceptable (e.g., `gpt-4o`, `claude-3-5-sonnet`). |
| **Adapter assist** (optional, sparingly) | Adapter authoring time only — not at runtime. | Vision and structured extraction for sites that don't parse cleanly. | Low. Used during dev, not prod. |

**The synthesizer is where cost matters.** The others are rare events.

## Provider abstraction

`worker/src/product_search/llm/__init__.py` exposes:

```python
def call_llm(
    *,
    provider: Literal["anthropic", "openai", "gemini", "glm"],
    model: str,
    system: str,
    messages: list[Message],
    response_format: Literal["text", "json"] = "text",
    max_tokens: int = 2048,
) -> LLMResponse:
    ...
```

Provider modules under `worker/src/product_search/llm/<provider>.py`. Each handles SDK init, the actual call, and translates errors into a common exception type.

GLM tip: use the OpenAI-compatible endpoint at `https://open.bigmodel.cn/api/paas/v4/`. Pass `base_url` to the OpenAI SDK; same call shape as OpenAI. This means the GLM provider module is ~20 lines.

## The benchmark

Phase 5 builds `worker/benchmark/`. It exists to answer one question: which model is cheapest among those that pass the bar?

### The bar

A passing model, given the synthesizer prompt and a fixture set of N≈10 verified-listing JSON inputs, must produce reports that:

1. **Have no fabricated data.** Every price, URL, MPN, and stock count in the report appears verbatim in the input JSON. Automated check: tokenize report numbers/URLs, intersect with input.
2. **Include every input row in the ranked table.** Automated check: compare row count.
3. **Sort the table correctly by `total_for_target_usd` ascending.** Automated check.
4. **Surface every flag in the input.** Automated check: every `flags[]` value appears in plain English somewhere in the report.
5. **Stay within ≤200 words of context narrative.** Automated check: word-count the "context" section.
6. **Produce valid markdown that renders.** Automated check: pass to a markdown lib without raising.

A model passes if it scores 100% on (1) and ≥9/10 fixtures pass (2)-(6).

### What the benchmark reports

For each `(provider, model)` combination configured:

- Pass/fail per criterion.
- USD cost per fixture run (computed from token counts × current published rates, stored in `worker/benchmark/pricing.yaml`).
- Latency p50/p95.
- Sample report excerpts.

A markdown report at `worker/benchmark/results/<date>.md` is committed.

### Initial model slate to benchmark

| Provider | Cheap synthesis candidate | Mid candidate (for onboarding) |
|---|---|---|
| Anthropic | `claude-haiku-4-5-20251001` | `claude-sonnet-4-6` |
| OpenAI | `gpt-4o-mini` (or current cheapest) | `gpt-4o` |
| Gemini | `gemini-2.0-flash` (or cheapest current) | `gemini-2.5-pro` |
| GLM | `glm-4.5-air` or `glm-4.5-flash` | `glm-4.5` (or `glm-4.6` if available) |

Exact model IDs are populated by the dev session running Phase 5 — pricing pages move and we want current values.

The user has flagged GLM as a likely winner on cost. The benchmark either confirms or refutes that — we don't pre-commit. If GLM passes the bar at meaningfully lower cost than the next-cheapest, GLM wins.

## How model choice gets configured

`worker/src/product_search/config.py` reads `LLM_SYNTH_PROVIDER` and `LLM_SYNTH_MODEL` from env (with sensible defaults). Same for `LLM_ONBOARD_PROVIDER` / `LLM_ONBOARD_MODEL`. Changing the model is a GitHub Actions secret update, not a code change.

The benchmark winner is also recorded in `docs/DECISIONS.md` with rationale, so the next session understands why we picked it.

## Re-running the benchmark

Re-run when:
- A new model lands at a vendor we use.
- Synthesizer prompt changes meaningfully.
- We notice the chosen model failing on real reports.

The benchmark uses cached fixtures so re-running is cheap. Cost per benchmark run across all 8 models is dollars, not tens.

## Hard rules across all LLM calls

1. **The LLM never has web access.** No tool use, no search, no browsing. Inputs are pre-fetched, pre-verified data only.
2. **The LLM never produces a price, URL, MPN, or stock count not present in its input.** This is enforced by the benchmark and by the synthesizer's automated post-check (which fails the run if a number in the report doesn't appear in the input).
3. **Prompts are checked into the repo.** Not strings in code; named files in `worker/src/product_search/synthesizer/prompts/` and `worker/src/product_search/onboarding/prompts/`. Diffs to prompts are version-controlled.
4. **No retry-on-failure for synthesis.** If the synthesizer produces output that fails the post-check, the run fails loudly. We'd rather miss a daily report than commit a fabricated one.

## Cost-control patterns we use

- **Prompt caching** where the provider supports it (Anthropic, OpenAI). The system prompt and profile hints are stable across calls — cache them.
- **Aggressive `max_tokens` ceilings.** Synthesizer reports are <2K tokens; cap there.
- **Batch where possible.** If two products are due in the same scheduler tick, the workflow runs them sequentially in the same job, sharing the cached prompt.
