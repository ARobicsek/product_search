# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger); **Phase 22 — Recall reliability under degraded AlterLab + onboarder robustness** (ADR-078/079/080, 2026-05-24); **Phase 23 — Hybrid filter restoration** (Part B closed, 2026-05-24).
- **IN PROGRESS:** **Phase 23 — Part A (headless E2E verification)**. Drove automated Selenium Chrome against the deployed app at `https://ari-product-search.vercel.app/onboard` to onboard throwaway slug `phase23-e2e-test`. Draft YAML successfully included Best Buy search and B&H detail URL (verifying ADR-079 detail preservation) plus `title_excludes: ["MX Master 3"]` fragile exclude (verifying ADR-080 emit). However, save button execution and subagent execution hit LLM quota rate limits (`RESOURCE_EXHAUSTED` 429), leaving E2E save+run verification to be resumed/completed once the quota resets (resets in ~4.5 hours).
- **Queued after:** **Phase 18 — Polish + second-product proof**.
- **Most recent work:** 2026-05-24 **Phase 23 — Hybrid filter restoration (Part B)**. In `worker/.../validators/pipeline.py`, deterministic pre-filters (`apply_filters`) now run and reject listings BEFORE the `ai_filter` runs, making hard rules (`condition_in`, `in_stock`, numeric boundaries, `title_excludes`) programmatic and absolute again. Semantic `ai_filter` is retained solely for fuzzy relevance. Rejections are logged without duplication. Written ADR-081. Added hybrid validator unit/regression tests. All 307 local worker pytest suite tests are passing green.


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

**Top priority: Phase 23 — Part A (headless E2E verification) resumption.** Since Part B is complete, the next session should complete Part A E2E verification (once LLM quota / rate limits reset in ~4.5 hours):
1. Run E2E onboarding against `ari-product-search.vercel.app` using a throwaway slug `phase23-e2e-test` (for "Logitech MX Master 3S mouse" with "new only" condition and "I don't want the older MX Master 3" fragile exclude).
2. Wait for the probes to finish and confirm the draft profile YAML contains Best Buy search, eBay search, and B&H detail URL (verifying ADR-079 detail preservation) plus `title_excludes: ["MX Master 3"]` (verifying ADR-080 emit).
3. Click "Save profile to repo" and verify that the save-time soft warning fires in the UI (verifying ADR-080 soft warning).
4. Open the product page at `/products/phase23-e2e-test`, click "Run now" to trigger the scraper, and poll until the GitHub Action completes.
5. Verify the generated report (no used listings, correct Best Buy price) and circuit breaker/retry logs in the Sources panel (verifying ADR-078).
6. Delete the throwaway slug via the home page delete button, run `git fetch origin` and confirm it is completely deleted from origin/main.


**Recall-maximization initiative — CLOSED.** ADR-076 (detail-URL backfill), ADR-077 (recall-first full-HTML search extraction), and the programmatic recall improvements all shipped earlier. Phase 22 (ADR-078/079/080) shipped 2026-05-24: R1 (AlterLab 5xx retry), R6 (per-run circuit breaker + budget), R2/R3 (probe advisory + registry detail-preference at the save gate), P1 (anti-fragile `title_excludes`). The 3 throwaway eval slugs were already absent from origin/main (removed empty local leftovers only).

- **Also still open (lower priority):** **T6** (re-measure B&H detail under the documented body shape — contained `cli probe-url` loop only); remaining eval items R4/R5 + P2–P4 if any. Full eval detail in memory `project_recall_precision_eval_2026_05_24.md`.


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
