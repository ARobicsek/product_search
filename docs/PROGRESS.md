# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger).
- **IN PROGRESS:** **Phase 21 — Extraction reliability** ([PHASES.md#phase-21](PHASES.md#phase-21--extraction-reliability-hard-site-render-hit-rate-proposed--confirm-design-before-coding)). Research done; T1 + safe retry shipped; **the big fix (documented AlterLab body shape) is USER-APPROVED (2026-05-21) and queued for next session** — see ADR-071.
- **Queued after:** **Phase 18 — Polish + second-product proof**.
- **Most recent work:** 2026-05-21 **Phase 21 R1/R2 + ADR-071**. Live AlterLab probes overturned the phase's assumed approach (see below).

## Current state — 2026-05-21 Phase 21 in progress (T1 + safe retry shipped; documented-shape migration is the next-session task)

**The headline finding (ADR-071, evidence-backed):** the production AlterLab calls are unreliable because of the **request body shape**, not (only) bot-walls.
- `wait_for` is a **phantom AlterLab param**: sending it (the registry's `wait_for: 5`, or any value) forces an async `202` job that never completes in our 120 s poll → **body 0**. This *is* the B&H/Target body-0 bug. Real knob = `advanced.wait_condition` (`networkidle`), returns sync 200.
- Legacy body shape (top-level `asp`/`country`/`min_tier:3`): Target detail **0/3** (202-hangs + a cached challenge stub). Legacy **`min_tier:4` is worse — 0/3, always 202-hangs** (so the "escalate to tier 4" idea in the original brief is wrong).
- **DOCUMENTED body shape** (`location.country` + `cost_controls.max_tier:"4"` + `wait_condition:networkidle`, keep `asp:true`, default cache): Target detail **3/3** with `$249.99`. ← the real fix.
- `cache:false` is harmful (0/3); leave cache default.

**Shipped this session (safe, green, no regression risk):**
- **T1** — `wait_for` → `wait_condition` end-to-end: `vendor_quirks.yaml` (bhphoto/newegg/microcenter), new `vendor_quirks.normalize_alterlab_options()` (migrates legacy `wait_for`→`networkidle`, validates `wait_condition` enum, clamps `min_tier` 1..4), runtime `_fetch_via_alterlab`, CLI (`--wait-condition`), onboarder tool schema + `onboard_v1.txt`, TS probe; web artifacts regenerated.
- **T2/T3** — cheap `_weak_render_reason` predicate + bounded retry-on-weak-render in the runtime (`_fetch_with_escalation`) and mirrored in the probe. **Escalation deliberately does NOT use `min_tier:4`** (proven harmful); until the documented-shape migration lands it only adds a harmless `networkidle` rung.
- **Refactor for T5** — pure helpers (`buildAlterlabBody`, weak-render, ladder, strip/price) extracted to new dependency-free `web/lib/onboard/alterlab-shared.ts` so a `node --test` parity guard can import them.
- **R1 doc** — `docs/ALTERLAB_OPTIONS.md` (full param reference + the legacy-vs-documented schism + measured hit-rates).
- Fixtures captured: `worker/tests/fixtures/universal_ai/{bh_silver-good,bh_silver-challenge,target_detail-challenge}-2026-05-21.html`.

**Checks:** worker `pytest` 285 passed, `ruff` + `mypy` clean; web `tsc` + `eslint` clean.

## Next session — start here

1. **Phase 21, the actual fix (USER-APPROVED 2026-05-21 — no further sign-off needed, implement directly): migrate the AlterLab wire body to the documented shape.** In `worker/.../adapters/universal_ai.py::_fetch_via_alterlab` AND `web/lib/onboard/alterlab-shared.ts::buildAlterlabBody`, build `{url, sync, formats:["html"], asp:true, location:{country}, cost_controls:{max_tier:"<tier>"}, advanced:{render_js:true, wait_condition}}` instead of top-level `country`/`min_tier`. Map registry `min_tier` → `cost_controls.max_tier` (string). Keep `asp:true`. Leave cache default. This is what turned Target detail **0/3 → 3/3**. Re-run the R2 harness logic to confirm ≥4/5 before/after.
2. **Make escalation use `cost_controls.max_tier`** (now that the body supports it) — restore a real tier-4 rung via the documented path (NOT legacy `min_tier:4`).
3. **T4** — multi-URL per vendor for multi-variant single SKUs (onboarder prompt + registry hint).
4. **T5** — write the probe↔runtime parity test: shared JSON fixture of `{options→expected_body}` + `{html→stripped/price-verdict}`, asserted by a Python test (pytest) and a `node --test --experimental-strip-types` script importing `alterlab-shared.ts` (verified locally to run on Node 22.16). Wire the node script into web CI.
5. **T6** — re-measure B&H detail under the documented shape; if still walled, record `known_failure` in `vendor_quirks.yaml`.
6. **E1–E4** — self-driven e2e: re-measure hit-rate; onboard a **throwaway** slug (`wh1000xm5-e2e-test`, NOT live `sony-wh-1000xm5`) via Chrome DevTools MCP; save + Run-now; assert correct Target price in the committed report; delete the test slug.
7. **Then** the prior queue: Schedule&Alerts editor prod verification (ADR-059/060/061), mobile popover layout, delete→reload spot check (ADR-063); then **Phase 18**.

> Throwaway harness `worker/_phase21_probe.py` (+ `_phase21_*.html/.json`) was deleted at wrap-up; its logic is summarized in ADR-071 / ALTERLAB_OPTIONS.md. Recreate from there if needed.

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
