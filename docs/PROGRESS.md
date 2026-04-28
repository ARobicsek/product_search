# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 1 — Profile schema + DDR5 profile**

See the Phase 1 brief in [PHASES.md](PHASES.md#phase-1--profile-schema--ddr5-profile).

## Current task

Define Pydantic `Profile` model in `worker/src/product_search/profile.py`, populate `products/ddr5-rdimm-256gb/profile.yaml` and `qvl.yaml`, add `cli validate <slug>` command, write schema tests.

## Last session

- Created `worker/` skeleton: `pyproject.toml` (Python 3.12), `src/product_search/__init__.py`, `cli.py`, `tests/__init__.py`, `tests/test_smoke.py`.
- Created `web/` skeleton: Next.js 15 App Router, Tailwind, ESLint, TypeScript (via `create-next-app`).
- Created `.github/workflows/ci.yml`: two parallel jobs — `worker` (ruff + mypy + pytest) and `web` (ESLint + tsc + next build).
- CI secrets presence-check step added (echoes key length, never value).
- All local checks green: 2 smoke tests pass, ruff + mypy clean, ESLint + tsc clean.
- Commit is **local only** (see next-session note below).

## Next session — start here

1. Read this file (you're doing it).
2. Read [PHASES.md § Phase 1](PHASES.md#phase-1--profile-schema--ddr5-profile).
3. **Before coding**, confirm that the Phase 0 commit has been pushed and CI is green on GitHub. If not, push first: `git push origin main`.
4. Phase 1 tasks (in order):
   - Define `Profile` Pydantic model in `worker/src/product_search/profile.py` from the schema in `products/_template/profile.yaml`.
   - Populate `products/ddr5-rdimm-256gb/profile.yaml` and `products/ddr5-rdimm-256gb/qvl.yaml`.
   - Add `cli validate <slug>` sub-command that runs the Pydantic model against the YAML file.
   - CI: add a step that runs `product-search validate ddr5-rdimm-256gb` on every commit touching `products/`.
   - Tests: schema accepts the DDR5 profile, rejects at least three malformed examples (missing field, unknown source ID, invalid cron).
5. Stop at end of Phase 1; do not start Phase 2.

## Open questions for the user

- The eBay Browse API requires registering an application at https://developer.ebay.com/. Phase 2 needs this; user can register at any point before Phase 2 starts. (Free tier is plenty.)
- Push notification "materiality" thresholds default to: any new cheapest path, ≥5% price drop, any new listing. User can override these in `products/<slug>/profile.yaml` under a future `alerts:` block.
- For Phase 11, generating VAPID keys: `npx web-push generate-vapid-keys` (one-time, store in Vercel env vars).
- **GH Actions secrets** — the four LLM keys exist in `.env`; copy them to repo secrets before the next CI run: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GLM_API_KEY`.

## Blockers

None.

## Noticed but deferred

- The handoff mentions Reddit r/homelabsales as a Tier C source; it requires Reddit API credentials. Add to env when adopted.
- The local `.env` file contains real LLM keys. It's gitignored. If those keys have been shared anywhere outside this machine, rotate them.

## Recently completed

- 2026-04-28: Phase 0 complete. `worker/` skeleton, `web/` Next.js scaffold, `.github/workflows/ci.yml` created. All local checks green (2 smoke tests, ruff, mypy, ESLint, tsc). Commit local — push + CI verification pending.
- 2026-04-28: Initial planning scaffold written. PLAN.md, all docs/, .gitignore, .env.example, README.md, CLAUDE.md, product profile template, DDR5 profile + QVL.
- 2026-04-28: Decisions confirmed (ADRs 003, 004, 005 → ACCEPTED). Added ADRs 010 (iOS PWA + web push) and 011 (adapter authoring philosophy). Phase plan updated.
- 2026-04-28: Pushed planning scaffold to GitHub.
