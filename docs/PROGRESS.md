# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger); **Phase 22 — Recall reliability under degraded AlterLab + onboarder robustness** (ADR-078/079/080, 2026-05-24).
- **IN PROGRESS:** **Phase 21 — Extraction reliability** ([PHASES.md#phase-21](PHASES.md#phase-21--extraction-reliability-hard-site-render-hit-rate-proposed--confirm-design-before-coding)). T1 + safe retry + documented body shape + tier-4 escalation + T5 parity guard + T4 multi-variant + **E2–E4 prod e2e (2026-05-21, ADR-074)** + **ADR-074 followup #1 (`condition_in` filter, 2026-05-23, ADR-075).** Remaining: **T6 only** (re-measure B&H detail under the migrated documented body shape).
- **Queued after:** **Phase 18 — Polish + second-product proof**.
- **Most recent work:** 2026-05-24 **Phase 22 (ADR-078/079/080)** — recall reliability. R1: `_fetch_via_alterlab` retries transient 5xx before the curl_cffi fallback (a 504 used to silently drop to a no-JS tier). R6: per-run circuit breaker (opens after 3 consecutive AlterLab-degraded sources) + wall-clock budget, reset by `_cmd_search`, skip reason in the Sources panel. R2/R3: probe is advisory — registry `force_detail_backup`/`prefer_page_type:detail` (+ new `PREFER_DETAIL_HOSTS`) sources are kept in `sources` on a probe failure (`detail-preference.ts`), prompt forbids detail→search swap. P1: `title-excludes-check.ts` save-time warning + prompt rule against name-substring / generic-component `title_excludes`. Diagnostic (3 spaced probes) confirmed AlterLab degraded-not-down. Green: worker ruff/mypy/305 pytest; web eslint/tsc/parity/guards/build. The 3 throwaway eval slugs were already absent from origin/main (removed empty local leftovers only).

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

**Recall-maximization initiative (ACCEPTED 2026-05-23 — user signed off on both ADRs; implement directly).** Current top priority. Philosophy: maximize recall at the fetch/extract stage; AlterLab + the Haiku filter are both cheap, so over-fetch is fine — the filter is NOT the recall bottleneck.

- **CLOSED:** **ADR-077** — recall-first search-step extraction: stop *gating* on the anchor walker. Full-rendered-HTML LLM extraction (verbatim-price-verified) implemented and unioned with JSON-LD + anchor walker. 297 tests pass cleanly, onboarding benchmark completed successfully.
- **CLOSED:** **ADR-076** — recall-first detail-URL backfill in the post-save background probe for ALL `force_detail_backup` vendors with search-only sources. Derive candidate detail URL(s) from search-page JSON-LD, add same-price variants up to 3, reject only clearly-wrong products. Deterministic per-SKU recall floor (defense-in-depth atop ADR-077). Successfully implemented, E2E tested (Dyson V15 onboarding/save/run verification), and pushed!
- **CLOSED:** **Programmatic Recall Improvements** (Refine AI relevance filter for product bundles/variants, log AlterLab 422 scrape bodies for auditing, and implement search keyword degradation fallback).
- **NEXT UP:** **T6 only** (re-measure B&H detail under the migrated documented body shape, contained `cli probe-url` loop only).
- **DONE (Phase 22, 2026-05-24):** the user-approved subset of the 2026-05-24 eval shipped as ADR-078/079/080 — R1 (AlterLab 5xx retry), R6 (per-run circuit breaker + budget), R2/R3 (probe advisory + registry detail-preference at the save gate), P1 (anti-fragile `title_excludes`). The 3 throwaway eval slugs (`airpods-pro-2-usb-c`, `logitech-mx-master-3s`, `kitchenaid-ksm150ps-mixer`) were already gone from origin/main this session (deleted out-of-band, presumably via the app since the eval) — only empty local leftover dirs remained, removed for hygiene; no repo change needed and the daily cron no longer has their profiles to run. **Still open from the eval (not in Phase 22):** R4/R5 + P2–P4 if any; and the architecture-drift note — `run_pipeline` routes ALL filtering through `ai_filter` (Haiku), bypassing the deterministic `filters.py` rejecters, so hard constraints are model-judgment not deterministic (its own ADR + sign-off). Full detail in memory `project_recall_precision_eval_2026_05_24.md`.


**Queue tail (after the recall initiative):** ADR-074 followup #2 (`description:` schema-vs-onboarder gap — optional-with-default or always-emit); ADR-074 followup #3 (Target search 0 candidates — largely subsumed by ADR-077); Schedule&Alerts editor prod verification (ADR-059/060/061); mobile popover layout; then **Phase 18**.

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
