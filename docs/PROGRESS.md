# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger).
- **IN PROGRESS:** **Phase 21 — Extraction reliability** ([PHASES.md#phase-21](PHASES.md#phase-21--extraction-reliability-hard-site-render-hit-rate-proposed--confirm-design-before-coding)). T1 + safe retry (prior session); **the headline fix — documented AlterLab body shape + tier-4 escalation — LANDED + live-verified 2026-05-21 (ADR-072).** Remaining: T4, T6, E2–E4.
- **Queued after:** **Phase 18 — Polish + second-product proof**.
- **Most recent work:** 2026-05-21 **documented-shape body migration (ADR-072)** — the 0/3→3/3 fix from ADR-071, now in the runtime + probe + a CI parity guard; live E1 confirmed.

## Current state — 2026-05-21 Phase 21: documented-shape migration LANDED + live-verified (ADR-072)

**Shipped this session (all green, live-verified):**
- **Documented-shape body migration (the ADR-071 headline fix).** `worker/.../adapters/universal_ai.py` now builds the AlterLab POST body via a new pure `_build_alterlab_body(url, opts)`, mapping flat internal keys → documented nested shape: `country`→`location.country`, `min_tier`→`cost_controls.max_tier` (string), `wait_condition`/`render_js`→`advanced.*`, keep `asp:true`, default cache. TS `buildAlterlabBody` (`web/lib/onboard/alterlab-shared.ts`) mirrors it; the probe inherits it (imports the shared helper).
- **Tier-4 escalation restored via the documented path.** Both ladders (`_escalation_ladder` / `alterlabEscalationLadder`) now add a 3rd rung `min_tier:4` → `cost_controls.max_tier:"4"` (fast sync 200, NOT the legacy 202-hanging top-level `min_tier:4`).
- **T5 probe↔runtime parity guard (anti-drift).** Shared fixture `worker/tests/fixtures/alterlab_parity/body_cases.json` asserted by BOTH `worker/tests/test_alterlab_parity.py` (pytest) and `web/scripts/check-alterlab-parity.test.mjs` (`node --test --experimental-strip-types`, wired into the web CI job as `npm run test:parity`). Would have caught the missing `asp` (ADR-070) instantly.
- Updated the Python body-shape + escalation-ladder tests for the new shape/tier-4 rung.

**Live E1 verification (single contained probe, no origin commit / no GH Action):** `cli probe-url <target …/A-86777236> --render --detail --country us --min-tier 4 --wait-condition networkidle` → origin 200, **1,544,723-char** render, Tier 1.5 extracted **Sony WH-1000XM5 — $249.99 (new)**. The migrated runtime path produces the predicted 3/3 result end-to-end.

**Checks:** worker `pytest` **286 passed**, `ruff` + `mypy` clean; web `tsc` + `eslint` (0 errors) clean; `npm run test:parity` green; `sync-prompt.js` → no artifact drift (registry untouched).

## Next session — start here

1. **T4 — multi-URL per vendor for multi-variant single SKUs** (onboarder prompt + optional `vendor_quirks` variant hint). No adapter change (multi-source already dedupes by canonical URL); cap ≤3 detail URLs/vendor.
2. **T6 — re-measure B&H detail under the now-migrated documented shape.** If still walled (Cloudflare), record `known_failure`/`prefer_page_type` in `vendor_quirks.yaml` and regenerate web artifacts. (Documented-shape B&H was never measured — R2 was cut short.)
3. **E2–E4 — self-driven prod e2e** (mutates origin/main + spends a GH-Action run, so deliberately deferred from the migration session): onboard a **throwaway** slug (`wh1000xm5-e2e-test`, NOT live `sony-wh-1000xm5`) via Chrome DevTools MCP; confirm the onboarder keeps Target search + detail backup; **save** + **Run-now**; assert the correct Target price in the committed `reports/<slug>/<date>.md` (post-check clean); then **delete** the test slug (Phase 16 button) and confirm `products/`+`reports/` are gone.
4. **Then** the prior queue: Schedule&Alerts editor prod verification (ADR-059/060/061), mobile popover layout, delete→reload spot check (ADR-063); then **Phase 18**.

> Re-running an R2-style N=5 hit-rate harness needs only the `cli probe-url` loop above; the throwaway harness was deleted — recreate from ADR-071 / ALTERLAB_OPTIONS.md if a full before/after table is wanted.

## Blockers

None blocking. CI is green (ADR-062 decoupled the worker suite + `validate-profiles` from app-mutable `products/`). Worker suite 280/280 green at `e0db48b`. One non-blocking follow-up bug is queued as the next session's task #1 (probe under-tests `page_type: "detail"` URLs → false demotion).

## Noticed but deferred (live only)

- **ADR-040 auto-demote implementation** — `source_runs` table + streak prune in `_cmd_search`. Deferred by ADR-040 itself; until it lands, 5 dead bose `universal_ai` URLs sit in active `sources` (~$0.011/run; manual demote is the stopgap).
- **ADR-053 deferred items #1–#3** — robust source-error handling / "N source(s) errored" surfacing (now also relevant to `while_below` + `total`-basis zero-listing skips).
- **Scheduled tick #25 (06:11Z) failure-with-nothing-due** — investigate when convenient.
- **No in-app signal for silent external-trigger death** — a redundant independent trigger (e.g. Cloudflare Workers cron) is the only true fix; user was offered it, deferred.
- **Email-on-alert** — deferred to its own ADR + sign-off (push delivery is confirmed working in prod, ADR-057).
- **Phase 5 benchmark fixtures vs live data** — synth picks not re-confirmed against live `anthropic/claude-haiku-4-5` payloads (per ADR-019; not blocking, live data proves Haiku works).
- **microcenter.com Cloudflare bypass** (vendor-level) — challenge page served at `min_tier: 3`; tier 4 → silent AlterLab failure (`body_len=0`). Now recorded as a `known_failure` in `worker/src/product_search/vendor_quirks.yaml` (the onboarder is told to route microcenter into `sources_pending`), but the underlying bypass is still UNSOLVED — needs deeper AlterLab tier investigation or an alternate anti-bot path.
- **bhphotovideo.com search-tile extraction mismatch** (vendor-level) — search page renders (200 KB, 24 product mentions) but `_extract_candidates` finds only 4 anchors. Worked around via `prefer_page_type: detail` in the registry (detail URLs route through the Tier 1.5 extractor), but the *search-tile* walker itself is still blind to B&H tiles — a `wait_for: <css-selector>` or B&H-specific extractor tweak would recover search coverage.

> Older "noticed but deferred" / open-questions / per-phase notes (Phase 10–12 era) live in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md); they were stale fossils, not live items.
