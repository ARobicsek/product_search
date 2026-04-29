# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 11 — iOS push notifications for alerts** (next session)

See the Phase 11 brief in [PHASES.md](PHASES.md#phase-11--ios-push-notifications-for-alerts).

## Current task

Wire up Web Push: VAPID keys, an "Enable alerts" subscription flow on the
installed PWA, a service-worker `push` handler with deep-link routing, an
authenticated `/api/push/notify` fan-out endpoint, and worker-side detection
of "material" diffs that triggers the notify call.

## Last session

- Phase 10 complete and verified end-to-end on production
  (https://ari-product-search.vercel.app). Onboarding interview wired:
  chat UI at `/onboard`, streaming proxy at `/api/onboard/chat` (Anthropic
  Sonnet 4.6 + hosted `web_search_20260209` tool, SSE stream),
  validate-and-commit endpoint at `/api/onboard/save` (TS-side schema
  validator mirrors `worker/src/product_search/profile.py`, then GitHub
  Contents API PUT).
- Live verification: ran a real onboarding interview for
  `supermicro-mbd-h13ssl-nt-o`. The flow produced two commits authored by
  the API (`fb9ec5a`, `b979ca1`). The committed profile passes both the
  TS-side validator AND `python -m product_search.cli validate` (canonical
  Pydantic). Web search fired correctly during the source-discovery turn.
- Canonical onboarding prompt at
  `worker/src/product_search/onboarding/prompts/onboard_v1.txt` (per
  LLM_STRATEGY hard rule #3), with `next.config.ts` `outputFileTracingIncludes`
  pointing Vercel's tracer at it so the prompt is bundled in the deployment.
- ADR-015 records the model choice (`anthropic:claude-sonnet-4-6`).
- New env vars in `.env.example`: `LLM_ONBOARD_PROVIDER`, `LLM_ONBOARD_MODEL`,
  `GITHUB_CONTENTS_TOKEN` (with `GITHUB_DISPATCH_TOKEN` as fallback).
- Follow-up fix `e894e8e`: post-save redirect to `/<slug>` was 404'ing
  because `getProductReports(slug)` returns `[]` until the first run lands
  a report. Fixed by (a) `getProducts()` now returns the union of slugs
  with reports + slugs with profiles, and (b) `[product]/page.tsx` renders
  a "Profile saved — Run now" empty state instead of `notFound()` when the
  profile exists but no reports do.
- All checks green: `tsc --noEmit`, `eslint`, `next build`, `pytest -q`
  (63 passed). Pushed to `origin/main` (`bd05fd5`, `e894e8e`).

## Next session — start here

1. Read this file.
2. Read [PHASES.md § Phase 11](PHASES.md#phase-11--ios-push-notifications-for-alerts).
3. Confirm with the user: VAPID keys (generate via `npx web-push generate-vapid-keys`)
   and Vercel KV provisioning before coding.
4. Implement the subscribe/notify flow per the phase brief. Use a distinct
   `PUSH_NOTIFY_SECRET` env var, NOT the same `WEB_SHARED_SECRET` exposed to
   the browser (per ADR-014's consequence).
5. Stop at end of Phase 11.

## Manual verification still needed for Phase 10

None — all verified on production with the Supermicro motherboard onboarding.

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
