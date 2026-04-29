# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 12 — Polish & second product proof** (in progress)

See the Phase 12 brief in [PHASES.md](PHASES.md#phase-12--polish--second-product-proof).

## Current task

Three follow-ups from the 2026-04-29 prod-test session, each best taken as
its own session:

- **Phase 12a** — Wire up at least one Tier-B source adapter
  (newegg, serversupply, memorynet, or theserverstore). Pattern follows
  the existing Phase 6 storefront adapters; capture a fixture and add tests.
- **Phase 12b** — Schedule editor UI on `/[product]` that writes
  `schedule.cron` back to the profile YAML via the GitHub Contents API
  (same pattern as `/api/onboard/save`).
- **Phase 12c** — Manage-sources UI: list current `sources[]` from the
  profile, allow toggling on/off, and re-invoke the Sonnet web search
  (existing `/api/onboard/chat`) to suggest more sources for the
  already-onboarded product.

After 12a–c, return to the original Phase 12 task: onboard a second product
end-to-end (suggestion: GPUs for AI inference, or PSUs ≥1600W Platinum).

## Last session

- Live prod-test of the DDR5 profile surfaced 9 issues. Knocked out the
  first wave of Phase 12 polish:
  - **Removed `WORKER_USE_FIXTURES: 1` from both prod workflows** so
    `/[product]` Run-now and the hourly scheduled tick now hit the live
    eBay Browse API and storefront URLs (ADR-017).
  - **Added a deterministic "Sources searched" panel** to the daily
    report so adapters that return 0 or error are still visible
    (ADR-018). Even when no listings pass the validator pipeline, a
    sources-only report is now written so the day isn't a blank entry.
  - **Improved Run-now UX** with a live elapsed-time counter and a
    tighter 2s poll interval for the first 30s after dispatch; full
    GH Actions run still takes 1–2 min for real reasons (queue + pip
    install + commit/push), but the user sees progress.
  - **Wired the custom favicon** via Next.js `app/icon.png` /
    `app/apple-icon.png` conventions, replacing the Next.js boilerplate
    `favicon.ico`.
- All 63 worker tests pass; `tsc --noEmit` clean; web ESLint regressions
  introduced this session were fixed back to the pre-existing baseline.
- Local commit only — push pending.

## Next session — start here

1. Read this file.
2. Read [PHASES.md § Phase 12](PHASES.md#phase-12--polish--second-product-proof).
3. Push the local commit, then **manually re-run the DDR5 search via
   "Run now" on the prod site** to verify the live eBay path actually
   works. Expect either a real report or surface-level breakage in the
   validator pipeline; capture findings before starting 12a/b/c.
4. Pick one of Phase 12a / 12b / 12c per the user's priority.
5. Stop at end of the chosen sub-phase.

## Manual verification still needed for Phase 11

- Install PWA to iOS Home Screen on a real device, enable alerts, and trigger an on-demand run that produces a material diff to ensure iOS successfully receives the push.





## Open questions for the user

- Push notification "materiality" thresholds default to: any new cheapest path, ≥5% price
  drop, any new listing. User can override these in `products/<slug>/profile.yaml` under a
  future `alerts:` block.
- **GH Actions secrets** — the four LLM keys exist in `.env`; copy them to repo secrets before
  the next CI run: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GLM_API_KEY`.
- **Z.AI account balance** — As of Phase 10, the Z.AI wallet is topped up so
  `glm-4.6` and `glm-5.1` are now callable. Re-running the Phase 5 benchmark
  with these two models is on the deferred list. Onboarding (Phase 10) still
  picked Anthropic Sonnet 4.6 over GLM 5.1 (see ADR-015) because GLM has no
  hosted web-search tool — switching to GLM there would mean wiring an
  external search backend.
- **Gemini free-tier rate limit** — the benchmark hit 429 on the very first Gemini call.
  Either set up Vertex AI billing or drop Gemini from the slate for now.

## Blockers

None.

## Noticed but deferred
- **Pre-existing worker lint/type errors.** `worker` mypy still reports
  2 errors in `adapters/memstore.py` and `adapters/cloudstoragecorp.py`
  (Phase 6 files, `url: str | None` passed to `Listing.url: str`), and
  ruff reports ~11 issues across worker tests (mostly unused `pytest`
  imports). These predate Phase 10. Consider a small clean-up pass in a
  future session — they don't block CI today but would block a stricter
  pre-commit hook later.
- **No TS unit-test framework in `web/`.** The Phase 10 schema validator
  was sanity-tested ad-hoc via a one-off node script; it lives in
  `web/lib/onboard/schema.ts` and would be a natural first TS unit-test
  if/when we add Vitest. Not urgent because CI re-runs `cli validate`
  on every commit that touches `products/`.
- **`target.configurations` schema is RAM-shaped.** The required keys
  (`module_count`, `module_capacity_gb`) make sense for DDR5 but are
  awkward for non-RAM products. The Supermicro motherboard onboarding
  filled them as `{module_count: 1, module_capacity_gb: 1}` — validates
  cleanly but is semantically meaningless. Worth a generalisation pass
  in a future session: rename to `unit_count`/`unit_size`, make the
  shape opaque, or add a `target_kind` discriminator. Update the
  Pydantic model, the TS validator, the onboarding prompt, AND the
  existing DDR5 profile in one go.
- **Synthesizer post-check is strict by design and rejects calculated comparisons** like
  "X is 7.7% cheaper than Y" and "$80 savings vs Micron." This is per ADR-001 and caught
  real issues across all three working models. After one prompt iteration adding "do NOT
  compute new numbers," GLM 4.5 Flash went 10/10. Anthropic Haiku 4.5 still produces
  occasional savings figures (~20% of fixtures) — useful as a fallback only when prompted
  even more strictly. Prompt iteration here is ongoing, not a blocker.
- **Benchmark fixtures are committed but use the synthesizer payload shape directly.** If
  `build_input_payload` ever changes shape, the fixtures need regeneration via
  `python -m benchmark.fixture_gen --force`.
- The handoff mentions Reddit r/homelabsales as a Tier C source; it requires Reddit API
  credentials. Add to env when adopted.
- The local `.env` file contains real LLM keys. It's gitignored. If those keys have been
  shared anywhere outside this machine, rotate them.
- Phase 4 used `unit_price_usd` for the 5% diff threshold. `total_for_target_usd` (the
  "cheapest path to target" cost) is arguably more user-meaningful but is `None` for any
  listing whose capacity doesn't match a profile configuration. If/when we want target-cost
  diffs, add a second threshold or fall back gracefully when `total_for_target_usd is None`.

## Recently completed

- 2026-04-29: Phase 12 polish wave 1. Removed `WORKER_USE_FIXTURES: 1` from
  prod workflows (ADR-017); added deterministic "Sources searched" panel
  to reports (ADR-018); added elapsed-time + tighter polling to the
  Run-now UX; replaced Next.js boilerplate favicon with the custom PWA
  icon. Tier-B adapter, schedule editor UI, and manage-sources UI deferred
  to Phase 12a/b/c. Local commit; push pending.
- 2026-04-29: Phase 11 complete. Implemented iOS push notifications for alerts via PWA subscription flow, Upstash Redis storage, and `web-push`. Material diff detection integrated into worker `cli.py`.
- 2026-04-29: Unblocked live eBay adapter by securing Production API keys and successfully fetching live DDR5 listings. Set up VAPID keys, Upstash Redis, and environment variables for Phase 11. Implementation plan approved and ready for next session.
- 2026-04-28: Phase 10 complete locally. `/onboard` chat UI + streaming
  `/api/onboard/chat` (Anthropic Sonnet 4.6 with hosted web_search) +
  `/api/onboard/save` (TS-side Pydantic-mirror validator + GitHub Contents
  API commit). New env vars: `LLM_ONBOARD_*`, `GITHUB_CONTENTS_TOKEN`. Local
  commit; push + Vercel env var setup + live E2E test pending.
- 2026-04-28: Phase 9 complete and verified end-to-end on Vercel (https://ari-product-search.vercel.app). "Run now" on `/[product]` triggers a real GH Actions workflow_dispatch, polls run status, and refreshes the report when complete. Toolbar resets to idle once the RSC refetch lands.
- 2026-04-28: Phase 8 complete. Built the PWA shell in Next.js, added Tailwind typography, configured github fetch helpers, and established the list and product detail routes.
- 2026-04-28: Phase 7 complete. Implement `scheduler-tick` CLI command to orchestrate runs across profiles matching the current UTC hour. Created GitHub Actions workflows for hourly crons and on-demand workflow_dispatch runs. Local commit; push pending.
- 2026-04-28: Phase 6 complete. Tier A adapters (Shopify API + selectolax eBay stores).
- 2026-04-28: Phase 5 complete. Synthesizer (prompt + post-check), 10-fixture benchmark with
  six bar criteria, runner across five `(provider, model)` combos. Winner: GLM 4.5 Flash
  (10/10, $0/run). `cli search` now writes `reports/<slug>/<date>.md`. 19 new tests (60
  passing total). Local commit; push pending.
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
