# product_search

A scheduled, auditable price-and-availability tracker for hard-to-source products. Deterministic scrapers find listings; an LLM only formats and explains the verified data. Designed to track **any product type** (RAM today, GPUs/CPUs/PSUs/anything tomorrow) via a config-driven profile.

## What it is, in one paragraph

You declare a product profile (what to buy, what counts as compatible, where to look). A worker runs on a schedule (daily, every 6h, or on demand) and pulls listings from per-source adapters. A validator pipeline rejects junk and flags risk. A small LLM synthesizer turns verified rows into a ranked daily report. A mobile-friendly web UI shows the latest report and lets you trigger a fresh run.

## Why this exists

Conversational LLM "research" reliably fabricates prices, stock counts, and quotes when asked to find listings. The fix isn't a stricter prompt — it's an architecture where the LLM never *claims* a number it didn't read from a real fetched page. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Where to start

| You are... | Read first |
|---|---|
| A new dev (or AI session) starting work | [docs/SESSION_PROTOCOL.md](docs/SESSION_PROTOCOL.md), then [docs/PROGRESS.md](docs/PROGRESS.md) |
| Trying to understand the system | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Adding a new product to track | [docs/PRODUCT_ONBOARDING.md](docs/PRODUCT_ONBOARDING.md) |
| Picking which LLM to use for a task | [docs/LLM_STRATEGY.md](docs/LLM_STRATEGY.md) |
| Deploying or scheduling | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| Wondering why we did X | [docs/DECISIONS.md](docs/DECISIONS.md) |

## Status

Phases 0-5 complete: scaffold, profile schema, eBay adapter (fixture mode), validator pipeline, SQLite + CSV storage, pure-Python diff, and the LLM synthesizer + multi-vendor benchmark. The Phase 5 benchmark picked **GLM 4.5 Flash** as the synth model (10/10 on the bar at $0/run); see [docs/DECISIONS.md](docs/DECISIONS.md) ADR-012. Next up is Tier-A seller adapters (Phase 6), with a brief design discussion first about source discovery for long-tail products. See [docs/PROGRESS.md](docs/PROGRESS.md) for live status.

## License

TBD.
