# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Phase 0 — Bootstrap repo** (in progress)

See the Phase 0 brief in [PHASES.md](PHASES.md#phase-0--bootstrap-repo).

## Current task

Scaffold empty `worker/` and `web/` packages, add CI, set GitHub Actions secrets. Initial planning scaffold has been pushed to GitHub.

## Last session

- Confirmed core decisions: Vercel + Next.js, GitHub Actions, eBay Browse API, public repo (implied by GH Actions free tier).
- Added two new ADRs: ADR-010 (iOS-installable PWA with web push) and ADR-011 (adapter authoring philosophy — clarifies "deterministic ≠ has API").
- Inserted Phase 11 (iOS push notifications); renumbered the prior Phase 11 to Phase 12.
- Phase 8 expanded to include PWA shell (manifest + service worker + Add to Home Screen).
- `.env.example` updated with VAPID keys and Vercel KV refs.
- Pushed planning scaffold to https://github.com/ARobicsek/product_search.

## Next session — start here

1. Read this file (you're doing it).
2. Read [PHASES.md § Phase 0](PHASES.md#phase-0--bootstrap-repo).
3. Phase 0 remaining tasks:
   - Create `worker/` skeleton: `pyproject.toml` (Python 3.12, deps placeholder), empty `src/product_search/__init__.py`, `tests/` folder.
   - Create `web/` skeleton: `package.json`, Next.js App Router boilerplate, Tailwind config.
   - Create `.github/workflows/ci.yml` with lint + type-check + test placeholders.
   - Set GitHub Actions repository secrets (the user will set these): `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GLM_API_KEY`, `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`. (eBay creds can wait until Phase 2; the four LLM keys exist in `.env` already and need to be copied to GH secrets.)
4. Stop at the end of Phase 0; do not start Phase 1 in the same session.

## Open questions for the user

- The eBay Browse API requires registering an application at https://developer.ebay.com/. Phase 2 needs this; user can register at any point before Phase 2 starts. (Free tier is plenty.)
- Push notification "materiality" thresholds default to: any new cheapest path, ≥5% price drop, any new listing. User can override these in `products/<slug>/profile.yaml` under a future `alerts:` block.
- For Phase 11, generating VAPID keys: `npx web-push generate-vapid-keys` (one-time, store in Vercel env vars).

## Blockers

None.

## Noticed but deferred

- The handoff mentions Reddit r/homelabsales as a Tier C source; it requires Reddit API credentials. Add to env when adopted.
- The local `.env` file contains real LLM keys. It's gitignored. If those keys have been shared anywhere outside this machine, rotate them.

## Recently completed

- 2026-04-28: Initial planning scaffold written. PLAN.md, all docs/, .gitignore, .env.example, README.md, CLAUDE.md, product profile template, DDR5 profile + QVL.
- 2026-04-28: Decisions confirmed (ADRs 003, 004, 005 → ACCEPTED). Added ADRs 010 (iOS PWA + web push) and 011 (adapter authoring philosophy). Phase plan updated.
- 2026-04-28: Pushed planning scaffold to GitHub.
