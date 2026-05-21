# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts, reopened/extended/re-closed 2026-05-17); **Phase 19** (universal adapter accuracy & vendor reach, 2026-05-17); **Phase 20** (reliable scheduling trigger — genuinely proven end-to-end 2026-05-18, ADR-052/054).
- **Queued (next phase):** **Phase 18 — Polish + second-product proof** ([PHASES.md](PHASES.md#phase-18--polish--second-product-proof-replaces-old-phase-12)).
- **Most recent work:** 2026-05-20 inter-phase fixes — ADR-065 (AlterLab custom params), ADR-067 (onboarder redundant detail-URL backup), plus profile-URL fixes for sony-wh-1000xm5 and an accessory-bundle `pack_size` guard. Full detail in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md).

## Current state — 2026-05-20 closed at `7515e77`; next session validates ADR-067

All 2026-05-20 follow-ups are on `origin/main`:
- `e93fd47` — sony-wh-1000xm5 profile URL fixes (target typo + bestbuy `&intl=nosplash`)
- `4c61507` — `_parse_pack` accessory-bundle guard + SYSTEM_PROMPT clarification (regression test added; worker 264/264 green)
- `7515e77` — ADR-067 onboarder prompt update for redundant detail-URL backup (no adapter changes)

After this session closed, the deployed app committed `c190034 chore: delete product sony-wh-1000xm5` — the user deleted the existing profile so they could **re-onboard the product from scratch** under the new ADR-067 behavior. Reviewing the resulting profile (whatever slug they use) is the next session's first task.

## Next session — start here

1. **Validate ADR-067 against the user's re-onboard attempt.** The user will arrive saying they tried onboarding a product again. Open the resulting profile under `products/<slug>/profile.yaml` and check:
   - For each stable-URL retail vendor (Target, Best Buy, Walmart, Amazon, …) that the user kept: are there **two** `universal_ai_search` source entries — one search URL and one `page_type: "detail"` URL? Both with the same `extra.alterlab_options`?
   - Did the onboarder skip the detail URL correctly when the product is multi-variant, when the vendor is eBay/marketplace, or when URLs aren't stable?
   - Did the onboarder tell the user in chat that it added a detail-URL backup (per the prompt's "Tell the user when you add a redundant detail URL" instruction)?
   - On the next scheduled run for that profile, is hit rate visibly better than the same product's prior runs?
   Likely failure modes if the LLM doesn't comply with the new prompt: it forgets the detail URL entirely; it picks a wrong / out-of-stock variant; it tries to add a detail URL on eBay (should refuse); the runtime then double-counts the same listing via search + detail (dedupe should catch this — verify in the data CSV).
   Source of the prompt change: [worker/src/product_search/onboarding/prompts/onboard_v1.txt](../worker/src/product_search/onboarding/prompts/onboard_v1.txt) (search "Redundant detail-URL backup"). Full rationale in [DECISIONS.md ADR-067](DECISIONS.md).
2. **Then** the prior queue still applies: prod test results for Schedule&Alerts editor (ADR-059/060/061) + mobile (~390px) popover layout verification; delete→reload spot check (ADR-063); then start **Phase 18 — Polish + second-product proof**.

## Blockers

None. CI is green (ADR-062 decoupled the worker suite + `validate-profiles` from app-mutable `products/`). Worker test suite ran 264/264 green at end of 2026-05-20 session.

## Noticed but deferred (live only)

- **ADR-040 auto-demote implementation** — `source_runs` table + streak prune in `_cmd_search`. Deferred by ADR-040 itself; until it lands, 5 dead bose `universal_ai` URLs sit in active `sources` (~$0.011/run; manual demote is the stopgap).
- **ADR-053 deferred items #1–#3** — robust source-error handling / "N source(s) errored" surfacing (now also relevant to `while_below` + `total`-basis zero-listing skips).
- **Scheduled tick #25 (06:11Z) failure-with-nothing-due** — investigate when convenient.
- **No in-app signal for silent external-trigger death** — a redundant independent trigger (e.g. Cloudflare Workers cron) is the only true fix; user was offered it, deferred.
- **Email-on-alert** — deferred to its own ADR + sign-off (push delivery is confirmed working in prod, ADR-057).
- **Phase 5 benchmark fixtures vs live data** — synth picks not re-confirmed against live `anthropic/claude-haiku-4-5` payloads (per ADR-019; not blocking, live data proves Haiku works).
- **microcenter.com Cloudflare bypass** (vendor-level, surfaced by sony-wh-1000xm5 on 2026-05-20) — challenge page served at `min_tier: 3`; bumping to tier 4 produces silent AlterLab failure (`body_len=0`). Affects any product the onboarder tries to route via microcenter.com. Needs deeper AlterLab tier investigation or alternate anti-bot path.
- **bhphotovideo.com structural extraction mismatch** (vendor-level, surfaced by sony-wh-1000xm5 on 2026-05-20) — full page renders (200 KB, 24 product mentions) but `_extract_candidates` finds only 4 anchors — search-result tiles aren't in the static-HTML shape the walker recognises. Likely needs a `wait_for: <css-selector>` against the tile container, or a B&H-specific extractor tweak.

> Older "noticed but deferred" / open-questions / per-phase notes (Phase 10–12 era) live in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md); they were stale fossils, not live items.
