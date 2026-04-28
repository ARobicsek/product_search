# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 6 — Tier A adapters** (next session)

See the Phase 6 brief in [PHASES.md](PHASES.md#phase-6--tier-a-adapters).

## Current task

Add the three Tier-A seller adapters (`nemixram`, `cloudstoragecorp`, `memstore`) so a live `cli search` produces listings from all four sources. **Before starting Phase 6 work**, take ~10 minutes on the deferred architecture question below — the answer affects how we build the new adapters.

### Pre-Phase-6 architecture question to consider

User concern raised at end of Phase 5: the current "user enumerates `sources:` in the profile" model works for DDR5 RAM (small, well-known seller list) but is too narrow for long-tail product types — handbags, pasta sauce, GPUs from random boutiques, etc. The proposal to consider: an **LLM-aided onboarding step** that uses web search at *onboarding time only* to suggest candidate sources for a new product type, which the user reviews and the system turns into adapter stubs. Runtime stays deterministic per ADR-001 — the LLM never extracts listings, only suggests where humans should look.

Open sub-questions to settle next session:
- Does this become part of Phase 10 (onboarding) or a new earlier phase?
- For categories with no parseable sources at all (Etsy artisans, IG DMs), do we accept the coverage gap or design a hybrid where LLM-proposed URLs get *re-fetched deterministically* before being treated as listings? Latter has its own hallucinated-URL failure mode.
- Should `LLM_STRATEGY.md` add a fourth call site for onboarding-time web search, and which provider has the cleanest tool-use API for it?

Decision should land as a new ADR in [DECISIONS.md](DECISIONS.md) before Phase 6 adapter work begins.

## Last session

- Finished Phase 5 (Synthesizer + multi-vendor benchmark).
- Added `worker/src/product_search/synthesizer/`: `prompts/synth_v1.txt` (the prompt), `synthesizer.py` (renders prompt, calls LLM, runs post-check that rejects any number/URL not in the input), `report.py` (writes `reports/<slug>/<date>.md`).
- Built `worker/benchmark/`: 10 fixture payloads, six bar criteria from [LLM_STRATEGY.md](LLM_STRATEGY.md), pricing table, runner that scores `(provider, model)` × fixtures and emits `worker/benchmark/results/<date>.md`.
- Ran the benchmark. **Winner: GLM 4.5 Flash** — 10/10 on the bar, $0/run (free tier). Recorded as ADR-012 in [DECISIONS.md](DECISIONS.md).
- Wired `LLM_SYNTH_PROVIDER` / `LLM_SYNTH_MODEL` via new `product_search.config` module. `cli search <slug>` now also runs the synthesizer and writes `reports/<slug>/<date>.md`. Added `--no-report` flag for offline runs.
- Test suite up to 60 passing (added 13 synthesizer + 6 benchmark-criteria tests). Ruff and strict mypy green across `src/`, `tests/`, and `benchmark/`.

## Next session — start here

1. Read this file.
2. Settle the pre-Phase-6 architecture question above. Land an ADR.
3. Read [PHASES.md § Phase 6](PHASES.md#phase-6--tier-a-adapters).
4. Phase 6 tasks (in order):
   - `adapters/nemixram.py` — Shopify `/products/<handle>.json` endpoints; storefront URL is in the profile.
   - `adapters/cloudstoragecorp.py` — eBay seller-store scrape with saved fixture HTML.
   - `adapters/memstore.py` — eBay seller-store scrape with the generic-MB-modules flag explicitly applied.
   - Tests against fixtures for each.
5. Stop at end of Phase 6.

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

- **Generality concern (carry into next session, see "Pre-Phase-6 question" above).** The
  current source-list-per-profile model works for narrow product types like DDR5 RAM but
  doesn't generalise to handbags, pasta sauce, or any category where sources are long-tail
  or unenumerable. Proposed fix: LLM-aided onboarding (web search to *suggest* sources, not
  extract listings). Decide before Phase 6 adapter work.
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
