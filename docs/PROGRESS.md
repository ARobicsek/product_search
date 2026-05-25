# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger); **Phase 22 — Recall reliability under degraded AlterLab + onboarder robustness** (ADR-078/079/080, 2026-05-24); **Phase 23 — Hybrid filter restoration + headless E2E verification** (both Parts A and B closed, 2026-05-24); **Phase 24 — Vendor-quirks coverage audit + Amazon JS-render fix** (ADR-082, 2026-05-24); **Phase 25 — "Explain the zero": classified source-outcome reasons + AlterLab 422 transient retry** (ADR-083/084, 2026-05-24); **Phase 26 — Cross-cutting LIVE stress test & regression sweep** (findings in [STRESS_TEST_26.md](STRESS_TEST_26.md), 2026-05-24); **Phase 27 — Fix the 3 Phase 26 defects + live re-verify** (ADR-085, verification in [STRESS_TEST_27.md](STRESS_TEST_27.md), 2026-05-25).
- **Queued next:** pick from the standing candidates below. Phase 28 closed 2026-05-25 (ADR-087).
- **Most recent work:** 2026-05-25 **Phase 28 closed** (ADR-087) — the two evidenced search-page recall leaks, diagnosed against freshly-captured committed fixtures, NO extractor code change. **Newegg: parser-gap premise REFUTED** — a `wait_condition:networkidle` MX Master 3S search capture (529 KB) carries ~20 product tiles and BOTH the anchor-walker AND ADR-077 full-HTML tiers recover the target (union 23 listings). Phase 26 Defect 6's "820 KB → 0" was a transient render miss under degraded AlterLab, not a structural gap; now regression-guarded by 2 fixture tests. **B&H: NOT recoverable today** — search is Cloudflare bot-walled (every render rung returned the SAME 31.7 KB "Performing security verification" interstitial, never products; same class as microcenter). Detail URLs carry B&H recall (`prefer_page_type:detail`, NOT `known_failure` — detail works); registry note strengthened + fixture test pins 0 priced candidates so the LLM can't fabricate off a challenge page.

## Current state — 2026-05-25 Phase 28 closed

**Deliverables:** ADR-087 in DECISIONS.md (diagnosis + regression-guarded). 3 new fixture tests in `worker/tests/test_universal_ai.py`; 2 new committed fixtures under `worker/tests/fixtures/universal_ai/` (`newegg_search_mx_master_3s.html` 529 KB rendered; `bhphotovideo_search_mx_master_3s.html` 31.7 KB Cloudflare challenge). `vendor_quirks.yaml` notes strengthened for `bhphotovideo.com` + `newegg.com` (regen'd `promptText.ts` via `sync-prompt.js`).

**What shipped:**
- `test_newegg_search_recall_substrate_present` (deterministic: ≥10 MX3S anchors, ≥8 priced walker candidates, ≥5 verbatim prices) + `test_newegg_search_offline_extracts_listings` (stubbed-LLM `fetch()` → ≥5 priced listings, target present, URLs verbatim).
- `test_bhphoto_search_is_cloudflare_walled_no_priced_candidates` (challenge fixture → 0 priced candidates, ≤5 titled anchors).
- Green: worker suite 339/339 (+3); ruff/mypy on the test file clean of new errors (pre-existing E501 ×4 + one `in`-operator error remain, untouched by this phase); web tsc 0 errors, eslint clean on the regen'd artifact, test:parity 2/2, test:guards 11/11, next build compiled.

**Finding worth remembering:** AlterLab was degraded again this session (B&H networkidle probes 504'd / returned 0-byte bodies; the usable B&H capture came via domcontentloaded). The Newegg "search is broken" premise that drove Phase 28 came from a single Phase 26 observation under that same degradation — a freshly-rendered page extracts fine. When a vendor reports 0 off a large body, suspect a transient render miss before a parser gap. Diagnostic spend ≈ $0.05.

## Standing candidates (pick up next; Phase 18 is the queued default)

1. **Phase 24 follow-up — same-class audit for the 3 hosts the consistency check flagged.** `centralcomputer.com`, `ebay.com`, `serversupply.com` all have `alterlab_known_good: true` without `default_alterlab_options` and log a WARNING at every registry load. eBay has its own dedicated adapter (probably benign — `universal_ai_search` rarely routes to ebay.com); the other two are universal_ai-only and likely need the same Amazon-class fix. Repeat the Phase 24 probe-and-add pattern. ~$0.003 in probes.
2. **B&H detail single-product URL returning 0 listings** (2026-05-24 Phase 23 Part A) — for `phase23-e2e-test`, the B&H MX Master 3S detail URL went through AlterLab `ok` but yielded 0 fetched/passed. The existing deferred item ("B&H *search-tile* mismatch") is about search, not detail. Worth investigating whether B&H detail-URL extraction (the Tier 1.5 extractor path) is also blind to B&H's tile structure on detail variant pages, or whether this was a one-off rendering miss. Probe via `cli probe-url` first to see whether it's `detailExtractable:true` today.
3. **Best Buy detail curl HTTP/2 INTERNAL_ERROR** (2026-05-24) — when AlterLab returns 4xx (e.g. 422), the fallback to curl_cffi hit `HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR (err 2)`. ADR-078 retries AlterLab 5xx but not 4xx (by design — 4xx is "wrong request shape"). Worth seeing if a single curl_cffi retry on this specific HTTP/2 error class would help; cheap, no AlterLab cost.
4. **ADR-074 followup #2** — `description:` schema-vs-onboarder gap: optional-with-default or always-emit from the prompt.
5. **ADR-074 followup #3** — Target search 0 candidates (largely subsumed by ADR-077, may already be dead).
6. **Backmarket Cloudflare-challenge investigation** (2026-05-24 Phase 24 probe) — `cli probe-url backmarket.com/en-us/search?q=...` through AlterLab at tier 3+networkidle returned a 32 KB "Just a moment..." challenge page; tier 4 produced the identical body. The registry now sends those defaults, but if the challenge persists Backmarket recall remains 0. May warrant a `known_failure` like microcenter, or a tier escalation experiment.
7. **Schedule & Alerts editor prod verification** (ADR-059/060/061).
8. **Mobile popover layout.**
9. **Onboarder schema paper-cuts** — ADR-074 followup #2 (`spec_attrs.required` / `description` gap that cost the stress26-ddr5 onboard a round-trip) + `low_seller_feedback` "(no description)" render bug. Small, cheap.

> **Phase 18 (second-product proof) was RETIRED 2026-05-25 (ADR-086)** — production already proves generality. **Phase 28 (Newegg + B&H search-page recall leaks) closed 2026-05-25 (ADR-087):** Newegg search works post-render (Defect 6 was transient); B&H search is Cloudflare-walled (recall via detail URLs). No code change needed; both regression-guarded.

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
- **microcenter.com Cloudflare bypass** (vendor-level) — challenge page served at `min_tier: 3`; tier 4 → silent AlterLab failure (`body_len=0`). Recorded as a `known_failure` in `worker/src/product_search/vendor_quirks.yaml` (onboarder routes microcenter into `sources_pending`). **Re-verified 2026-05-25 (Phase 27 D3): 0/3 distinct detail URLs succeeded at registry defaults — the Phase 26 one-off success was a cache hit; block KEPT at `blocker`.** Underlying bypass still UNSOLVED — needs deeper AlterLab tier investigation or an alternate anti-bot path.
- **bhphotovideo.com search is Cloudflare bot-walled** (vendor-level, ADR-087, re-verified 2026-05-25) — every render rung (tier 3/4 × networkidle/domcontentloaded) returns the SAME 31.7 KB "Performing security verification" interstitial, never the product grid. Same class as microcenter. NOT recoverable today via search; recall comes from detail URLs (`prefer_page_type:detail`). Earlier partial renders also leaked tiles to the static walker (4 of 24 anchors). Unblock needs an AlterLab anti-Cloudflare path for B&H or a dedicated adapter (out of scope).

> Older "noticed but deferred" / open-questions / per-phase notes (Phase 10–12 era) live in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md); they were stale fossils, not live items.
