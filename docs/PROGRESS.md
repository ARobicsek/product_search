# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 5 — Synthesizer + multi-vendor benchmark**

See the Phase 5 brief in [PHASES.md](PHASES.md#phase-5--synthesizer--multi-vendor-benchmark).

## Current task

Build the synthesizer prompt + post-check, run the multi-vendor benchmark across the four cheap-tier LLMs, pick the cheapest passing one, and start writing daily reports to `reports/<slug>/<date>.md`.

## Last session

- Finished Phase 4 (Storage + diff vs yesterday).
- Added `worker/src/product_search/storage/` package: `db.py` (SQLite, composite PK on `(url, fetched_at)`), `csv_dump.py` (daily CSV mirror at `worker/data/<slug>/<date>.csv`), `diff.py` (pure-Python diff with default 5% `unit_price_usd` threshold).
- Wired `cli search` to insert the validated listings into SQLite and write the daily CSV (skipped when `--no-store`).
- Added `cli diff <slug>` — computes new / dropped / price-changed between the two most recent snapshot dates in the local SQLite store.
- Test suite up to 41 passing (added 13 in `test_storage.py`); ruff and strict mypy green.
- Folded in two leftover Phase 3 cleanups that hadn't been committed: `Listing.title` is now populated by the eBay adapter, and PROGRESS.md was already retargeted to Phase 4.

## Next session — start here

1. Read this file.
2. Read [PHASES.md § Phase 5](PHASES.md#phase-5--synthesizer--multi-vendor-benchmark).
3. Phase 5 tasks (in order):
   - Write `worker/src/product_search/synthesizer/prompts/synth_v1.txt` — prompt from [ARCHITECTURE.md](ARCHITECTURE.md).
   - Implement `synthesizer/synthesizer.py`: render prompt with today's listings + diff + profile hints, call the configured LLM, post-check rejects any number/URL/MPN not in the input.
   - Build `worker/benchmark/`: ≥10 fixture listing-set JSONs, test bar checks per [LLM_STRATEGY.md](LLM_STRATEGY.md), pricing table, runner.
   - Run benchmark across all four providers' cheap-tier models. Record the choice in [DECISIONS.md](DECISIONS.md) with cost data.
   - Wire chosen model via `LLM_SYNTH_PROVIDER` / `LLM_SYNTH_MODEL` env vars.
   - `cli search <slug>` writes `reports/<slug>/<date>.md` after the diff step.
4. Stop at end of Phase 5.

## Open questions for the user

- The eBay Browse API requires registering an application at https://developer.ebay.com/.
  Phase 2 needs this; user can register at any point before Phase 2 starts. (Free tier is plenty.)
- Push notification "materiality" thresholds default to: any new cheapest path, ≥5% price
  drop, any new listing. User can override these in `products/<slug>/profile.yaml` under a
  future `alerts:` block.
- For Phase 11, generating VAPID keys: `npx web-push generate-vapid-keys` (one-time, store in
  Vercel env vars).
- **GH Actions secrets** — the four LLM keys exist in `.env`; copy them to repo secrets before
  the next CI run: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GLM_API_KEY`.

## Blockers

None.

## Noticed but deferred

- The handoff mentions Reddit r/homelabsales as a Tier C source; it requires Reddit API
  credentials. Add to env when adopted.
- The local `.env` file contains real LLM keys. It's gitignored. If those keys have been
  shared anywhere outside this machine, rotate them.
- Phase 4 used `unit_price_usd` for the 5% diff threshold. `total_for_target_usd` (the
  "cheapest path to target" cost) is arguably more user-meaningful but is `None` for any
  listing whose capacity doesn't match a profile configuration. If/when we want target-cost
  diffs, add a second threshold or fall back gracefully when `total_for_target_usd is None`.

## Recently completed

- 2026-04-28: Phase 4 complete. SQLite store, CSV dump, pure-Python diff engine, `cli diff`
  command, 13 new tests (41 passing total). Local commit; push pending.
- 2026-04-28: Phase 3 complete. Validator pipeline (filters, flags, QVL, total-for-target).
- 2026-04-28: Phase 2 complete. Listing model, LLM abstraction, eBay adapter (fixture mode).
- 2026-04-28: Phase 1 complete. Pydantic Profile model, validate CLI, 10 tests (ruff + mypy
  + pytest all green). Commit local — push + CI verification pending.
- 2026-04-28: Phase 0 complete. `worker/` skeleton, `web/` Next.js scaffold,
  `.github/workflows/ci.yml` created. All local checks green (2 smoke tests, ruff, mypy,
  ESLint, tsc). Commit local — push + CI verification pending.
- 2026-04-28: Initial planning scaffold written. PLAN.md, all docs/, .gitignore, .env.example,
  README.md, CLAUDE.md, product profile template, DDR5 profile + QVL.
- 2026-04-28: Decisions confirmed (ADRs 003, 004, 005 → ACCEPTED). Added ADRs 010 (iOS PWA +
  web push) and 011 (adapter authoring philosophy). Phase plan updated.
- 2026-04-28: Pushed planning scaffold to GitHub.
