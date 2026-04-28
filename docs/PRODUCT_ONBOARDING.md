# Product Onboarding

How a new product type gets added to the system, with or without writing YAML by hand.

## What a product profile encodes

Every product the system tracks lives at `products/<slug>/profile.yaml`. The profile answers six questions:

1. **What is it?** — name, slug, one-line description.
2. **What do you want?** — target quantity and any valid alternative configurations.
3. **What counts as compatible?** — hard rejects (form factor, voltage, ECC, etc.) and soft warnings (China shipping, low-feedback sellers, "SmartMemory" branding, etc.).
4. **Where do we look?** — which adapters to invoke and with what queries.
5. **Reference data** — paths to known-compatible part lists (QVL/HCL), seller allowlists/blocklists, etc.
6. **Domain notes** — synthesis hints the report should surface (e.g., "for this CPU, channel population matters less than naive bandwidth math suggests").
7. **Schedule** — cron expression(s) for scheduled runs.

The schema is in [products/_template/profile.yaml](../products/_template/profile.yaml). The first concrete example is [products/ddr5-rdimm-256gb/profile.yaml](../products/ddr5-rdimm-256gb/profile.yaml).

## Two ways to onboard a product

### A. By hand (Phase 1 onward)

Copy `products/_template/profile.yaml` into `products/<slug>/profile.yaml`. Fill it in. Add any reference data files alongside. The CLI validates the profile (`python -m product_search.cli validate <slug>`) before letting you run it.

This is the path Phase 1 builds. Adding a product type by hand is fine forever — the interview is sugar on top.

### B. By interview (Phase 10)

The web UI runs a multi-turn conversation that produces a draft profile. The user reviews, edits if needed, and clicks "Save" — the web app commits `products/<slug>/profile.yaml` (and any reference files) via the GitHub API.

The interview is LLM-driven. The system prompt for the interview is checked into the repo at `worker/src/product_search/onboarding/prompt.py`. It instructs the model to:

- Ask one or two questions at a time, not a wall of questions.
- Probe for hard requirements vs. nice-to-haves explicitly.
- Ask for reference URLs (QVL, manufacturer pages, etc.) and note that the user can paste them in.
- Surface any source it knows about that fits the product type, and ask permission before adding.
- Never invent technical specs the user didn't provide.
- Output the draft profile YAML in a single, complete block at the end, with comments preserved.

A typical interview takes ~10 turns. Sample shape:

```
Q: What product do you want to track? Brief, one line.
A: 24GB+ NVIDIA GPUs for local AI inference.

Q: What system are you putting it in? Power budget, slot count?
A: Single GPU, RTX Pro 6000 (so 600W headroom in PSU). x16 PCIe Gen 5.

Q: New only, or used acceptable? International shipping acceptable?
A: New preferred, used OK from US sellers only. No international.

Q: Specific models to consider, or any 24GB+ card?
A: 4090, 5090, RTX 6000 Ada, RTX Pro 6000.

Q: Any sellers you trust or want excluded?
A: Trust: Newegg, B&H, Amazon (sold by Amazon), Microcenter. Avoid: eBay
   non-business sellers.

[continues...]

Q: Daily, or more often?
A: Daily.

Output: draft profile.yaml shown for review.
```

### What the interview model needs to be capable of

- Multi-turn conversation with state (the spec being built up).
- Producing valid YAML matching the schema.
- Asking, not assuming.

This is a mid-tier capability requirement. See [LLM_STRATEGY.md](LLM_STRATEGY.md) — onboarding is the call site that justifies a slightly more capable model than synthesis does.

## Adding sources during onboarding

Each profile's `sources` list names adapters by ID and gives them parameters. When the interview suggests a new source the system doesn't yet have an adapter for:

1. The interview surfaces the URL pattern and asks the user to confirm.
2. The web UI saves the suggestion to `products/<slug>/profile.yaml` under `sources_pending: []` (not `sources:`).
3. A future dev session writes the adapter. Until it's written, the source is skipped at runtime with a warning.

This means the onboarding flow doesn't need to write new adapter code — that stays a code-change task. The interview just captures intent.

## Validation

`python -m product_search.cli validate <slug>` checks:

- Profile YAML matches the schema (Pydantic model in `worker/src/product_search/profile.py`).
- All adapters referenced in `sources:` exist. Adapters in `sources_pending:` are listed but not required.
- Reference files (e.g. `qvl_file:`) exist.
- Cron expressions parse.

CI runs `validate` on every commit that touches `products/`.

## Conventions for new product profiles

- **Slug**: lowercase, hyphenated, descriptive. `ddr5-rdimm-256gb`, `gpu-24gb-inference`, `psu-1600w-platinum`.
- **One profile = one buying job.** If you have two builds with different targets, use two profiles. Don't try to parameterize one profile with branches.
- **Synthesis hints are short and load-bearing.** They go in the daily report; they should be ≤2 sentences each, and only included if they materially change the buying decision.
- **Reference data is small and committed.** A `qvl.yaml` of 50 part numbers is fine. A scraped 10MB compatibility database is not — that goes in storage and gets fetched at runtime.
