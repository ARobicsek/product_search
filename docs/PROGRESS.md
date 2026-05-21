# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger).
- **IN PROGRESS:** **Phase 21 — Extraction reliability** ([PHASES.md#phase-21](PHASES.md#phase-21--extraction-reliability-hard-site-render-hit-rate-proposed--confirm-design-before-coding)). T1 + safe retry + documented body shape + tier-4 escalation + T5 parity guard all landed (prior sessions); **T4 multi-variant detail-URL redundancy LANDED 2026-05-21 (ADR-073).** Remaining: T6, E2–E4.
- **Queued after:** **Phase 18 — Polish + second-product proof**.
- **Most recent work:** 2026-05-21 **T4 (ADR-073)** — onboarder prompt now adds up to 3 cosmetic-variant detail URLs per vendor (instead of skipping the detail backup for multi-variant products), for more independent render attempts. Prompt-only.

## Current state — 2026-05-21 Phase 21: T4 multi-variant detail-URL redundancy LANDED (ADR-073)

**Shipped this session (prompt-only, all green):**
- **T4 — multi-variant single-SKU detail-URL redundancy.** `worker/.../onboarding/prompts/onboard_v1.txt`: removed the "multi-variant ⇒ skip the redundant detail URL" rule; replaced it with guidance to add the search URL PLUS up to **3** cosmetic-variant detail URLs (color/finish, same price, user indifferent), each a `page_type:"detail"` `universal_ai_search` source, preferred variant first, kept only if `detailExtractable:true`. Cap ≤3 detail URLs/vendor. Carve-out preserved: spec variants (capacity/size/RAM/trim) or a hard variant requirement ("must be black") → track ONLY the wanted variant. No adapter change (multi-source already dedupes by canonical URL + takes cheapest passing).
- **No `vendor_quirks` change** (the brief's "optional variant hint" — declined; multi-variant is a generic product property, not a per-vendor quirk, and enriching `force_detail_backup` from a bool would force TS-consumer changes for no gain). Registry untouched → `vendor-quirks-data.ts` correctly did not regenerate; only `promptText.ts` did.

**Checks:** worker `pytest` **286 passed**; web `tsc` + `eslint` (0 errors, 4 pre-existing SW warnings) clean; `npm run test:parity` green; `sync-prompt.js` regenerated only `promptText.ts`.

## Next session — start here

1. **T6 — re-measure B&H detail under the now-migrated documented shape.** If still walled (Cloudflare), record `known_failure`/`prefer_page_type` in `vendor_quirks.yaml` and regenerate web artifacts. (Documented-shape B&H was never measured — R2 was cut short.)
2. **E2–E4 — self-driven prod e2e** (mutates origin/main + spends a GH-Action run, so deliberately deferred): onboard a **throwaway** slug (`wh1000xm5-e2e-test`, NOT live `sony-wh-1000xm5`) via Chrome DevTools MCP; confirm the onboarder keeps Target search + detail backup (and, per T4, multiple B&H color detail URLs); **save** + **Run-now**; assert the correct Target price in the committed `reports/<slug>/<date>.md` (post-check clean); then **delete** the test slug (Phase 16 button) and confirm `products/`+`reports/` are gone.
3. **Then** the prior queue: Schedule&Alerts editor prod verification (ADR-059/060/061), mobile popover layout, delete→reload spot check (ADR-063); then **Phase 18**.

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
