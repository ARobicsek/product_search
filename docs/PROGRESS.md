# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 12 — Polish & second product proof** (live prod path proven; sub-phases queued)

See the Phase 12 brief in [PHASES.md](PHASES.md#phase-12--polish--second-product-proof).

## Status as of end of 2026-04-29 session

The full prod pipeline is now working end-to-end on live data for the
DDR5 profile. Run-now → live eBay Browse API → 161 passing listings →
truncated to top-30 → Anthropic Haiku 4.5 synthesis → canonical URL
post-check → committed report rendered on Vercel. Verified on the page:
bottom-line analysis, 21-row ranked listings table with real prices
($625–$1,250 range), real eBay URLs, sources-searched panel.

This was a 5-wave debug arc — see "Recently completed" below for the
sequence and what each fix did. All 65 worker tests pass; web tsc
clean; pushed through `b2b23d3`.

## Current task — pick one of these for next session

- **Review test results for Universal AI Extraction**: The AI pipeline was deployed. Next session will review its output on test queries to ensure it extracts and filters correctly.
- **Phase 12b** — **Wire up a Tier-B source adapter** (newegg,
  serversupply, memorynet, or theserverstore). Pattern follows the
  existing Phase 6 storefront adapters; capture a committed fixture
  and add tests. Broadens coverage beyond eBay.
- **Phase 12c** — **Schedule editor UI** on `/[product]` that writes
  `schedule.cron` back to the profile YAML via the GitHub Contents
  API (same pattern as `/api/onboard/save`).
- **Phase 12d** — **Manage-sources UI**: list current `sources[]`
  from the profile, allow toggling on/off, and re-invoke the Sonnet
  web search (existing `/api/onboard/chat`) to suggest more sources
  for the already-onboarded product.
- **Phase 12 original** — Onboard a second product end-to-end
  (suggestion: GPUs for AI inference, or PSUs ≥1600W Platinum).

Recommended order: 12b (more coverage) or 12c (schedule UI is small and high-value),
then the original Phase 12 second-product onboard.

## Open follow-ups (deferred during this session)

- **CI on `main` is chronically red** on lint steps (worker ruff +
  web ESLint). Predates Phase 12; PROGRESS already tracked this as
  deferred. Worth a small cleanup pass — this session noted it
  again but didn't fix.
- **Phase 5 benchmark fixtures should be re-run against
  `anthropic / claude-haiku-4-5`** to formally re-confirm the synth
  picks 10/10 there too (per ADR-019). Not blocking; live data is
  proving Haiku works.

## Next session — start here

1. Read this file.
2. Read [PHASES.md § Phase 12](PHASES.md#phase-12--polish--second-product-proof).
3. Skim ADRs 017, 018, 019, 020 from this session to understand
   the synth pipeline's current invariants.
4. Pick one of Phase 12a / 12b / 12c / 12d per the user's priority.
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

- 2026-04-30: Phase 12a (Storefront silent-fail diagnostic & GitHub Actions push fix).
  - Fixed a race condition in `search-on-demand.yml` and `search-scheduled.yml` where pushing the generated report would fail with `[rejected] main -> main (fetch first)` if the repository was updated during execution. Added `git pull --rebase origin main` before `git push`.
  - Identified and fixed silent failures in `nemixram`, `cloudstoragecorp`, and `memstore` adapters. They previously returned an empty list `[]` on non-200 HTTP statuses. Changed to explicitly raise `RuntimeError`, allowing `cli.py` to correctly surface the error in the "Sources searched" report panel.
  - Fixed unit tests broken by Phase 12's introduction of `ai_filter`. Bypassed the LLM call in `ai_filter.py` when `WORKER_USE_FIXTURES=1` to keep tests deterministic and pass without requiring LLM credentials.
- 2026-04-30: Phase 12 (Universal AI Extraction and Filtering).
  - Designed and deployed a "best of both worlds" pipeline (ADR-021).
  - Replaced explicit CSS scraping with `universal_ai_search`, using GLM-5.1 to extract JSON from raw HTML.
  - Mitigated hallucination by strictly enforcing that LLM-extracted URLs exist verbatim in the source HTML.
  - Replaced deterministic `apply_filters` with `ai_filter`, offloading complex spec evaluations to GLM-5.1 before passing the surviving listing objects to Claude Haiku for report synthesis.
  - Set up persistent `.jsonl` trace logging for all LLM calls in `worker/data/llm_traces/`.
- 2026-04-29: Phase 12 wave 6 (Profile Edit Mode & Synthesizer Fixes).
  - Implemented the **Profile Edit Mode** in the Web UI. Users can now click "Edit Profile" on any product page, which loads the existing `profile.yaml` from GitHub and passes it into the Onboarding AI context. The AI can then apply natural language edits (e.g. "avoid 16GB cards").
  - Fixed GitHub Contents API PUT failing on overwrites by automatically fetching the existing file `sha` before committing.
  - Synced `title_excludes` down to the `web` validation schema, matching the python schema.
  - Reverted synthesizer LLM from GLM-5.1 back to `claude-haiku-4-5` to avoid overly verbose Chain-of-Thought output in the generated markdown. Added strict prompt rules explicitly forbidding planning text ("Analyze the Request", etc.) while condensing the `URL` and `Source` column into a single markdown hyperlink.
  - Fixed a classic clock-skew bug in `RunNowButton` polling where the Vercel server's dispatched timestamp was slightly ahead of GitHub Action's `created_at` timestamp, causing the frontend to wait indefinitely. Also added `run-name` to the dispatch workflow.

- 2026-04-29: Phase 12 wave 5 (stale-cache hotfix on the web side).
  - Wave 4 actually fixed the synth — the 2026-04-29 report on disk has
    a full bottom-line, 21-row ranked listings table, and sources
    panel. The Vercel page kept showing the empty-output diagnostic
    because `getReportContent` and `getProductReports` in
    `web/lib/github.ts` used `next: { revalidate: 3600 }`. The 1-hour
    data cache silently masked the first prod-data success.
    `revalidatePath('/[product]')` from `/api/revalidate` invalidates
    the route-segment render cache but not necessarily underlying
    data fetches without a tag. Switched both reads to
    `cache: 'no-store'` — a 10KB markdown report fetched from GitHub
    raw on every page load is fine for this app's volume.

- 2026-04-29: Phase 12 wave 4 (synth post-check canonicalisation).
  - **Smoking-gun finding**: even Claude Haiku 4.5 — well-documented for
    verbatim copy on tabular tasks — failed the post-check on a live
    eBay URL. The "fabricated URL" was identical to a payload URL on
    `scheme + host + path`; only the tracking query string
    (`?_skw=...&hash=item...&amdata=enc%3A...`) differed. The post-check,
    not the model, was wrong.
  - **Fix**: post-check now uses canonical URL comparison — scheme +
    lowercased host + path, with trailing slash stripped. Tracking
    params no longer cause false-positive "fabrication" errors. The
    strict guarantee on prices/quantities/MPNs is unchanged. ADR-020
    documents the refinement (does not supersede ADR-001).
  - **Diagnostic**: when the post-check now fails, the worker dumps the
    offending URL and its canonical form to stderr so the next failure
    is debuggable from the GH Actions log without code edits.
  - 2 new tests added; all 65 worker tests pass.

- 2026-04-29: Phase 12 wave 3 (synth provider swap).
  - **Confirmed root cause** of empty/garbage prod synth output via
    fresh GH Actions log: post-`bd4d005`, GLM 4.5 Flash *was*
    producing output (recovered via the new `reasoning_content`
    fallback) but its output included **hallucinated eBay URLs** with
    munged tracking parameters. ADR-001's strict post-check correctly
    rejected the output. Live eBay URLs have long
    `?_skw=...&hash=item...&amdata=enc%3A...` query strings; GLM
    isn't reproducing them verbatim.
  - **Switched synth default to `anthropic / claude-haiku-4-5`**
    (ADR-019, supersedes the model choice in ADR-012). Cost is
    ~$0.001/run; ANTHROPIC_API_KEY is already wired through both
    workflows. GLM remains supported as a provider for future
    benchmarking. The synth model is env-overridable
    (`LLM_SYNTH_PROVIDER` / `LLM_SYNTH_MODEL`) so reverting is one
    workflow edit.

- 2026-04-29: Phase 12 wave 2.
  - **Confirmed eBay live path works in prod**: 186 fetched, 160 passed
    after EBAY_CLIENT_ID/SECRET were added to GH Actions repo secrets.
  - **Fixed synthesizer choke on 100+ listings**: cap synth input to top
    SYNTH_MAX_LISTINGS=30 (sorted by total_for_target_usd) and bumped
    `max_tokens` from 2048 → 4096. The Phase 5 prompt was tuned against
    fixtures of ~5–10 listings; with 160 the LLM produced empty output,
    which passed the post-check (no fabricated numbers in nothing) and
    wrote a near-blank report. The full set remains in SQLite and the
    daily CSV; the report now appends a note when truncation applies.
    Empty-synth output is now caught explicitly (italicised note in
    place of the bottom line) instead of silently producing a blank
    report.
  - **Fixed sw.js Response.clone() bug**: SWR branch was cloning
    inside an async caches.open().then() callback after the page had
    already consumed the body. Cloned synchronously and excluded
    /api/* + non-GET requests from the SW cache.

  Open follow-ups from this session:
  - **Storefront adapters returning 0 in prod live mode**:
    nemixram_storefront, cloudstoragecorp_ebay, memstore_ebay all
    reported `fetched: 0` with no error. Each has a silent-fail path
    (e.g. nemixram returns `[]` on any non-200 from
    `/products.json`). Needs targeted diagnostic — possibly add error
    logging that surfaces to the sources panel, or capture a fresh
    fixture to compare against.
  - **CI on `main` is chronically red** on lint steps (worker ruff +
    web ESLint). Predates Phase 12; PROGRESS already tracked this as
    deferred. Worth a small cleanup pass.

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
