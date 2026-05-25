# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger); **Phase 22 — Recall reliability under degraded AlterLab + onboarder robustness** (ADR-078/079/080, 2026-05-24); **Phase 23 — Hybrid filter restoration + headless E2E verification** (both Parts A and B closed, 2026-05-24); **Phase 24 — Vendor-quirks coverage audit + Amazon JS-render fix** (ADR-082, 2026-05-24); **Phase 25 — "Explain the zero": classified source-outcome reasons + AlterLab 422 transient retry** (ADR-083/084, 2026-05-24); **Phase 26 — Cross-cutting LIVE stress test & regression sweep** (findings in [STRESS_TEST_26.md](STRESS_TEST_26.md), 2026-05-24); **Phase 27 — Fix the 3 Phase 26 defects + live re-verify** (ADR-085, verification in [STRESS_TEST_27.md](STRESS_TEST_27.md), 2026-05-25).
- **Queued next:** **Phase 18 — Polish + second-product proof** (the standing next-up; see brief in PHASES.md).
- **Most recent work:** 2026-05-25 **Phase 27 closed.** Shipped D1+D2+D3 (commit `0974299`). **D1** (reinforces ADR-079): prompt rule + new deterministic save-guard (`detail-preference-presence.ts`) so a detail-preferred URL can't be dropped to a URL-less `sources_pending` placeholder before the save gate runs. **D2** (reinforces ADR-084): cli now stamps `source_url` into each `Listing.attrs` and keys `passed` attribution by `(source, host, url)`, so same-host error rows aren't swallowed by a sibling URL's success. **D3** (maintains ADR-068): re-probed microcenter 0/3 at registry defaults → KEPT `known_failure: blocker` with a 2026-05-25 re-verification note (Phase 26 success was a cache-hit outlier). Live re-verify drove one throwaway `stress27-mx3s` onboard+run via Chrome DevTools MCP under a degraded-AlterLab session: D1 PASS (B&H kept in `sources` with `probe_note`, surfaced as a `transient` bullet in the ADR-084 callout, mobile render clean), D2 PASS (unit test primary + live no-regression), D3 PASS (probe-evidence). Slug deleted via Phase 16 path (`b2664f0`); live products untouched. Spend ≈ $0.17.

## Current state — 2026-05-25 Phase 27 closed

**Deliverables:** [docs/STRESS_TEST_27.md](STRESS_TEST_27.md) (per-defect PASS/FAIL + commit pointers); [docs/microcenter_reprobe_2026_05_25.md](microcenter_reprobe_2026_05_25.md) (D3 probe evidence); ADR-085 in DECISIONS.md (reinforces ADR-079/084, maintains ADR-068).

**What shipped (commit `0974299`):**
- D1: `onboard_v1.txt` prompt rule (regen'd `promptText.ts`) + `web/lib/onboard/detail-preference-presence.ts` wired into `/api/onboard/save` + 5 new cases in `check-onboard-guards.test.mjs` (11/11).
- D2: `cli.py` stamps `source_url` into `Listing.attrs`; `_passed_match_key` → `(source, host, url)`; regression test `test_build_zero_reason_callout_includes_per_source_httperror` + updated key tests (test_cli.py 17/17, worker suite 336/336).
- D3: `vendor_quirks.yaml` microcenter `known_failure` re-verification note (regen'd web artifacts).
- Green: ruff + mypy clean on cli.py; web tsc 0 errors, eslint pre-existing-only, test:parity 2/2, test:guards 11/11, next build compiled.

**Live re-verify finding worth remembering:** AlterLab was degraded the whole session (pool exhaustion, 504s on detail probes, 2.3 KB Amazon stubs). The first stress27-mx3s onboard (before the new prompt deployed) reproduced the exact Phase 26 D1 regression — URL-less B&H `sources_pending` placeholder — and the second onboard (new prompt live) kept B&H in `sources` with `probe_note`. Clean before/after. A B&H detail probe hung one onboard for ~14 min; if onboards hang again, use a "don't probe, here are pre-probed results" message to skip the slow live probing.

## Standing candidates (pick up next; Phase 18 is the queued default)

1. **Phase 24 follow-up — same-class audit for the 3 hosts the consistency check flagged.** `centralcomputer.com`, `ebay.com`, `serversupply.com` all have `alterlab_known_good: true` without `default_alterlab_options` and log a WARNING at every registry load. eBay has its own dedicated adapter (probably benign — `universal_ai_search` rarely routes to ebay.com); the other two are universal_ai-only and likely need the same Amazon-class fix. Repeat the Phase 24 probe-and-add pattern. ~$0.003 in probes.
2. **B&H detail single-product URL returning 0 listings** (2026-05-24 Phase 23 Part A) — for `phase23-e2e-test`, the B&H MX Master 3S detail URL went through AlterLab `ok` but yielded 0 fetched/passed. The existing deferred item ("B&H *search-tile* mismatch") is about search, not detail. Worth investigating whether B&H detail-URL extraction (the Tier 1.5 extractor path) is also blind to B&H's tile structure on detail variant pages, or whether this was a one-off rendering miss. Probe via `cli probe-url` first to see whether it's `detailExtractable:true` today.
3. **Best Buy detail curl HTTP/2 INTERNAL_ERROR** (2026-05-24) — when AlterLab returns 4xx (e.g. 422), the fallback to curl_cffi hit `HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR (err 2)`. ADR-078 retries AlterLab 5xx but not 4xx (by design — 4xx is "wrong request shape"). Worth seeing if a single curl_cffi retry on this specific HTTP/2 error class would help; cheap, no AlterLab cost.
4. **ADR-074 followup #2** — `description:` schema-vs-onboarder gap: optional-with-default or always-emit from the prompt.
5. **ADR-074 followup #3** — Target search 0 candidates (largely subsumed by ADR-077, may already be dead).
6. **Backmarket Cloudflare-challenge investigation** (2026-05-24 Phase 24 probe) — `cli probe-url backmarket.com/en-us/search?q=...` through AlterLab at tier 3+networkidle returned a 32 KB "Just a moment..." challenge page; tier 4 produced the identical body. The registry now sends those defaults, but if the challenge persists Backmarket recall remains 0. May warrant a `known_failure` like microcenter, or a tier escalation experiment.
7. **Schedule & Alerts editor prod verification** (ADR-059/060/061).
8. **Mobile popover layout.**
9. Then **Phase 18 — Polish + second-product proof.**

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
- **bhphotovideo.com search-tile extraction mismatch** (vendor-level) — search page renders (200 KB, 24 product mentions) but `_extract_candidates` finds only 4 anchors. Worked around via `prefer_page_type: detail` in the registry (detail URLs route through the Tier 1.5 extractor), but the *search-tile* walker itself is still blind to B&H tiles — a `wait_for: <css-selector>` or B&H-specific extractor tweak would recover search coverage.

> Older "noticed but deferred" / open-questions / per-phase notes (Phase 10–12 era) live in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md); they were stale fossils, not live items.
