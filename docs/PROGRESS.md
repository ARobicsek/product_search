# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger).
- **IN PROGRESS:** **Phase 21 — Extraction reliability** ([PHASES.md#phase-21](PHASES.md#phase-21--extraction-reliability-hard-site-render-hit-rate-proposed--confirm-design-before-coding)). T1 + safe retry + documented body shape + tier-4 escalation + T5 parity guard + T4 multi-variant + **E2–E4 prod e2e (2026-05-21, ADR-074)** + **ADR-074 followup #1 (`condition_in` filter, 2026-05-23, ADR-075).** Remaining: **T6 only** (re-measure B&H detail under the migrated documented body shape).
- **Queued after:** **Phase 18 — Polish + second-product proof**.
- **Most recent work:** 2026-05-23 **ADR-074 followup #1 (ADR-075)** — new deterministic `condition_in` filter rule (worker + TS mirror) so a stated "new only / no used / no refurbished / no open-box" hard requirement becomes a real YAML filter; onboarder prompt now emits it; save-time soft warning fires when the chat `<state>` ledger records a condition requirement that's absent from the draft's `spec_filters`. Build green (worker 287 pytest, web tsc/eslint/parity/build); live-LLM in-app test blocked locally by an edge-runtime env-loading quirk (covered in prod).

## Current state — 2026-05-21 Phase 21: E2–E4 prod e2e PASSED (ADR-074)

**Verified this session against `ari-product-search.vercel.app`:**
- **Target detail URL extracted `$249.99` live in the committed report's `.filter.jsonl`** — same price ADR-071 predicted, now produced by the deployed adapter (ADR-072 documented-shape body in production). The row was correctly post-check-rejected by `in_stock failed: quantity_available is 0` (Target reports Black variant OOS today). Phase 21's "Target detail probe hit-rate materially up" criterion is now satisfied end-to-end, not just in the contained E1.
- **Best Buy detail backup → $248.00, B&H Black detail URL → $248.00** — ADR-067 redundancy is doing its job in prod.
- **T4 multi-variant working as designed**: onboarder offered Black/Silver/Smoky Pink B&H detail URLs (ADR-073's new behavior), probe correctly demoted Silver/Pink as `detailExtractable:false` (still Cloudflare-walled, will be the focus of T6) and kept Black.
- **Delete clean**: throwaway `products/wh1000xm5-e2e-test/` + `reports/wh1000xm5-e2e-test/` gone from origin in one commit; live `sony-wh-1000xm5` untouched (ADR-063 still working).

**Followups noticed this session (queued, not blocking) — full detail in ADR-074:**
1. **Onboarder doesn't translate "new only" hard requirement into a YAML `condition` filter** — user said "new only, no refurbished/open-box/used" in chat but saved YAML had only `spec_filters: [in_stock]`. Result: 24 of 30 ranked rows were used eBay listings (cheapest = used "ALWAYS LOW BATTERY" Sony at $89.99). Fix at the onboarder prompt + profile-schema layer.
2. **Save-time validator requires `description:` but onboarder LLM omits it on first draft** — first Save returned `profile failed schema validation: description: expected string`. Make `description:` optional w/ default, OR have the prompt include it from turn 1. Concrete UX paper-cut on every new onboard.
3. **Target search URL fetches 0 candidates** — documented-shape body fixes Target *detail*, but Target's search-tile walker still gets nothing (`target.com | ok | 0 | 0`). ADR-067 detail backup compensates for now; investigate alongside B&H search-tile (the existing deferred item).

## Next session — start here

**Recall-maximization initiative (PROPOSED, 2026-05-23 — user-driven; confirm scope before code).** Current top priority. Philosophy: maximize recall at the fetch/extract stage; AlterLab + the Haiku filter are both cheap, so over-fetch is fine — the filter is NOT the recall bottleneck (it batches at 50, evaluates every listing, no cap; it only loses precision). Recall is won/lost in the search step. Two complementary ADRs:

1. **ADR-077 FIRST (biggest lever)** — recall-first search-step extraction: stop *gating* on the anchor walker (`_extract_candidates`; Target search→0, B&H→4/24). Add full-rendered-HTML LLM extraction (verbatim-price-verified to keep the no-fabrication boundary), unioned with JSON-LD + anchor walker, `wait_condition:networkidle` default. Worker-only; needs committed Target/B&H search fixtures asserting recall ≥N where the walker found 0/4. Lifts recall for every product on hard-to-parse vendors AND unblocks ADR-076 on pure-SPA vendors.
2. **ADR-076 SECOND** — recall-first detail-URL backfill in the post-save probe for ALL `force_detail_backup` vendors with search-only sources; derive candidate detail URL(s) from search-page JSON-LD, add same-price variants up to 3, reject only clearly-wrong products. Deterministic per-SKU recall floor (defense-in-depth atop ADR-077).
3. Do these in SEPARATE sessions — ADR-077 is the larger/riskier (core adapter + fixtures + verbatim guard) and must not be rushed alongside ADR-076. One phase per session (SESSION_PROTOCOL).

**Queue tail (after the recall initiative):** T6 (B&H detail N=5, contained `cli probe-url` only); ADR-074 followup #2 (`description:` schema-vs-onboarder gap — optional-with-default or always-emit); ADR-074 followup #3 (Target search 0 candidates — largely subsumed by ADR-077); Schedule&Alerts editor prod verification (ADR-059/060/061); mobile popover layout; then **Phase 18**.

> ADR-075 (`condition_in`) was verified live in prod 2026-05-23: a "new only" onboard produced `spec_filters: [condition_in: [new], in_stock]`; the condition-drift warning correctly stayed silent (no false positive). The ADR-067 detail-backup warning fired as designed for that run's search-only Target/Best Buy URLs — which motivated ADR-076 above.

> The R2-style N=5 hit-rate harness needs only the `cli probe-url` loop documented in ADR-071 / `docs/ALTERLAB_OPTIONS.md`.

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
