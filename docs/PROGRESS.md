# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 8 — Web UI MVP** (next session)

See the Phase 8 brief in [PHASES.md](PHASES.md#phase-8--web-ui-mvp).

## Current task

Build the PWA shell (Next.js App Router, mobile-first Tailwind) and render the daily reports fetched from GitHub.

## Last session

- Finished Phase 7 (Scheduling).
- Added `_cron_matches_hour` and `_cmd_scheduler_tick` to `cli.py` to run `search` in isolated subprocesses for profiles whose cron expressions match the current UTC hour.
- Created GitHub Actions workflows: `.github/workflows/search-scheduled.yml` (runs hourly) and `.github/workflows/search-on-demand.yml` (runs via `workflow_dispatch`).
- The workflows and CLI updates are committed locally. Pushing is deferred per session protocol.

## Next session — start here

1. Read this file.
2. Ensure you have the user's explicit approval to push the Phase 7 commits to the remote repository.
3. Test the on-demand trigger via `gh workflow run search-on-demand --field product=ddr5-rdimm-256gb` and verify it runs successfully.
4. Read [PHASES.md § Phase 8](PHASES.md#phase-8--web-ui-mvp) (installable PWA shell).
5. Build the `web/` Next.js frontend, PWA manifest, and basic `/` + `/[product]` routes.
6. Stop at end of Phase 8.

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
- **Z.AI account balance** — Phase 5 benchmark surfaced that `glm-4.6` and `glm-5.1` both
  return "余额不足或无可用资源包" (insufficient balance) on this account. Only `glm-4.5-flash`
  has free quota. Top up the Z.AI wallet if a smarter GLM is wanted as an upgrade path.
- **Gemini free-tier rate limit** — the benchmark hit 429 on the very first Gemini call.
  Either set up Vertex AI billing or drop Gemini from the slate for now.

## Blockers

None.

## Noticed but deferred
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
