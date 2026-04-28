# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 2 — Worker skeleton + LLM abstraction + first adapter**

See the Phase 2 brief in [PHASES.md](PHASES.md#phase-2--worker-skeleton--llm-abstraction--first-adapter).

## Current task

Build the `Listing` dataclass in `worker/src/product_search/models.py`, add the
`llm/` abstraction layer with `cli llm-ping <provider> <model>`, and write the
first eBay Browse API adapter with a saved fixture.

## Last session

- Defined Pydantic `Profile` model in `worker/src/product_search/profile.py`:
  allow-listed source IDs, filter/flag rules, cron expression validation.
- Defined `QVL` / `QVLEntry` model and `load_qvl()` helper.
- Implemented `cli validate <slug>`: loads + validates profile.yaml + qvl.yaml,
  exits 0 / 1 / 2 appropriately.
- Added `pydantic>=2.7`, `pyyaml>=6.0` runtime deps; `types-PyYAML>=6.0` dev dep.
- Wrote `worker/tests/test_profile.py`: 8 tests (2 real-file happy-path, 1
  minimal fixture, 4 rejection cases, 1 CLI integration).
- Added `validate-profiles` CI job; runs `validate ddr5-rdimm-256gb` on every push/PR.
- All local checks green: ruff, mypy (strict), pytest 10/10.
- Commit is **local only** (see next-session note below).

## Next session — start here

1. Read this file (you're doing it).
2. Read [PHASES.md § Phase 2](PHASES.md#phase-2--worker-skeleton--llm-abstraction--first-adapter).
3. **Before coding**, confirm the Phase 1 commit has been pushed and CI is green
   on GitHub. If not, push first: `git push origin main`.
4. Phase 2 tasks (in order):
   - `worker/src/product_search/models.py` — `Listing` dataclass per ARCHITECTURE.md.
   - `worker/src/product_search/llm/__init__.py` + per-provider modules. Add
     `cli llm-ping <provider> <model>` (a hello-world round-trip).
   - `worker/src/product_search/adapters/ebay.py` — eBay Browse API adapter.
     Save raw response to `worker/tests/fixtures/ebay/<descriptive>.json`.
   - `cli search ddr5-rdimm-256gb --no-validate --no-store` prints Listing rows
     as JSON to stdout.
5. Stop at end of Phase 2; do not start Phase 3.

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

## Recently completed

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
