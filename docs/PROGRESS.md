# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger); **Phase 22 — Recall reliability under degraded AlterLab + onboarder robustness** (ADR-078/079/080, 2026-05-24); **Phase 23 — Hybrid filter restoration + headless E2E verification** (both Parts A and B closed, 2026-05-24).
- **Queued next:** **Phase 18 — Polish + second-product proof**.
- **Most recent work:** 2026-05-24 **Phase 23 — Part A headless E2E verification PASSED.** Drove Chrome DevTools MCP against `ari-product-search.vercel.app` to onboard throwaway slug `phase23-e2e-test` for "Logitech MX Master 3S mouse" with "new only" + fragile-exclude prompt; ran the scraper; deleted the slug. All Phase 22 ADRs verified live. See current state below.

## Current state — 2026-05-24 Phase 23 Part A: headless E2E verification PASSED

**Verified live this session against `ari-product-search.vercel.app`** (single 4m 32s run; $0.009 search-side cost + ~$0.10 onboarding):

- **ADR-079 (detail-preference at save gate) ✓** — onboarder probed B&H detail URL, got a weak response ("very short body, likely a redirect or geofence"); the registry detail-preference + advisory-probe rule kept `https://www.bhphotovideo.com/c/product/1703321-REG/logitech_910_006558_mx_master_3s_pale.html` in `sources` with `page_type: detail` instead of demoting it. Visible in the saved YAML at e43eecb.
- **ADR-080 (anti-fragile `title_excludes`) ✓** — onboarder emitted `title_excludes: ["MX Master 3"]` (a substring of the product name "MX Master 3S") despite the prompt rule. Save-time deterministic guard fired with the exact warning from `title-excludes-check.ts`: *"A title_excludes value (\"MX Master 3\") is a substring of the product name — it will reject the target product itself and silently zero recall."* Profile still saved (soft warning, not blocker) — exactly the designed behavior. Was then manually edited out of the profile before Run-now so the recall test wasn't zeroed.
- **ADR-078 (AlterLab 5xx retry + per-run circuit breaker + budget) — armed, not exercised today.** Run completed in 4m 32s (well under the 600s budget); no 3-consecutive AlterLab degradations to trip the breaker. AlterLab appears healthier than during the 2026-05-24 eval. Sources panel did surface a Best Buy detail curl HTTP/2 INTERNAL_ERROR clearly (visibility working).
- **ADR-081 (Hybrid filter restoration) — alive in prod.** Filter log entries name both `relevance_check` and `condition_in` in their pass reasons; 3/3 passing listings all `new` condition; no used listings emitted.

**Recall observation for MX Master 3S (single data point):** Best Buy search carried the entire product (3/3 valid listings, cheapest $88.99 Bluetooth Edition Black). B&H detail and both Amazon sources returned 0 listings; Best Buy detail URL hit a curl HTTP/2 INTERNAL_ERROR on the curl_cffi fallback (AlterLab returned 4xx → fell through). So recall on this product is **single-vendor dependent**. Matches the longstanding B&H search-tile + Amazon anti-bot deferred items.

**Delete clean:** `phase23-e2e-test` profile + report + data fully removed from origin (commit 77a9fe7); empty `git ls-tree -r --name-only origin/main | grep phase23`. No live cron will fire on a test slug.

## Next session — start here

**Top priority:** the queue tail below — pick one and go. No carry-over from Phase 23.

**Queue (lower priority but live):**
1. **ADR-074 followup #2** — `description:` schema-vs-onboarder gap: optional-with-default or always-emit from the prompt.
2. **ADR-074 followup #3** — Target search 0 candidates (largely subsumed by ADR-077, may already be dead).
3. **B&H detail single-product URL returning 0 listings** (NEW, 2026-05-24 Phase 23 Part A) — for `phase23-e2e-test`, the B&H MX Master 3S detail URL went through AlterLab `ok` but yielded 0 fetched/passed. The existing deferred item ("B&H *search-tile* mismatch") is about search, not detail. Worth investigating whether B&H detail-URL extraction (the Tier 1.5 extractor path) is also blind to B&H's tile structure on detail variant pages, or whether this was a one-off rendering miss. Probe via `cli probe-url` first to see whether it's `detailExtractable:true` today.
4. **Best Buy detail curl HTTP/2 INTERNAL_ERROR** (NEW, 2026-05-24) — when AlterLab returns 4xx (e.g. 422), the fallback to curl_cffi hit `HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR (err 2)`. ADR-078 retries AlterLab 5xx but not 4xx (by design — 4xx is "wrong request shape"). Worth seeing if a single curl_cffi retry on this specific HTTP/2 error class would help; cheap, no AlterLab cost.
5. **Schedule & Alerts editor prod verification** (ADR-059/060/061).
6. **Mobile popover layout.**
7. Then **Phase 18 — Polish + second-product proof.**

> ADR-075 (`condition_in`) was verified live in prod 2026-05-23 and re-confirmed 2026-05-24 (Phase 23 Part A: `condition_in: [new]` emitted into the saved YAML and visible in the filter log's pass reasons).

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
