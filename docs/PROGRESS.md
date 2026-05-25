# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger); **Phase 22 — Recall reliability under degraded AlterLab + onboarder robustness** (ADR-078/079/080, 2026-05-24); **Phase 23 — Hybrid filter restoration + headless E2E verification** (both Parts A and B closed, 2026-05-24); **Phase 24 — Vendor-quirks coverage audit + Amazon JS-render fix** (ADR-082, 2026-05-24); **Phase 25 — "Explain the zero": classified source-outcome reasons + AlterLab 422 transient retry** (ADR-083/084, 2026-05-24); **Phase 26 — Cross-cutting LIVE stress test & regression sweep** (findings in [STRESS_TEST_26.md](STRESS_TEST_26.md), 2026-05-24).
- **Queued next:** **Phase 27 — Fix the 3 Phase 26 defects + live re-verify** (user-requested 2026-05-24; full brief in PHASES.md). Bundles D1 (ADR-079 hole — onboarder drops detail-preferred URL before save gate), D2 (ADR-084 per-source `passed` host-aggregation bug), D3 (microcenter `known_failure` is stale). Then **Phase 18 — Polish + second-product proof.**
- **Most recent work:** 2026-05-24 **Phase 26 closed.** Onboarded 4 throwaway `stress26-*` slugs spanning the vendor + product matrix (single-SKU "new only", multi-variant cosmetic, component/kit, microcenter-known_failure), drove all 4 via Chrome DevTools MCP, ran them through the deployed `search-on-demand.yml` workflow, walked the per-ADR regression checklist against the produced reports + filter logs, and verified web + mobile rendering at 375 px. Findings: most ADRs verified firing in production (068/075/077/078/081/082/084). Three real defects (one P1 onboarder + one P1 classifier + one P2 registry drift) and three paper-cuts captured for a follow-up session, NOT implemented (Phase 26 is verification, per brief). All 4 throwaway slugs deleted via Phase 16 path; live products untouched. Total spend ~$0.62 — well under the $3–8 budget.

## Current state — 2026-05-24 Phase 26 closed

**Deliverable:** [docs/STRESS_TEST_26.md](STRESS_TEST_26.md) — per-row PASS/FAIL/N·A regression checklist + prioritised defect list + screenshot evidence ([stress26_mobile_callout_mx3s.png](stress26_mobile_callout_mx3s.png)).

**ADR regression checklist — verified firing in production this session:**
- ADR-068 (Best Buy `intl=nosplash` URL transform, microcenter `known_failure` routing into `sources_pending`)
- ADR-075 (`condition_in:[new]` emission + deterministic rejection of refurbished/used — 47 rejections on the mx3s run alone)
- ADR-077 (full-HTML extraction on Amazon search yielded 20 candidates / 5 passing on mx3s; the anchor walker alone would return 0)
- ADR-078 (per-run circuit breaker fired after 3 consecutive degraded Best Buy detail fetches on xm5; subsequent sources skipped with visible reason)
- ADR-081 (Hybrid filter pre-pass rejects used/refurb deterministically before ai_filter — visible in `[condition_in]` prefix in filter.jsonl)
- ADR-082 (Amazon defaults `country: us, min_tier: 3, wait_condition: networkidle` present in saved profile + visibly working at runtime)
- ADR-083 (`browser_pool_exhausted` 422 detected indirectly via the `alterlab_pool_exhausted` diagnostic flag surfacing in the ADR-084 callout)
- ADR-084 (every non-clean source got a classified reason in the `[!NOTE]` callout; categories `transient` / `needs work` / `no match` all observed across the 4 reports; web + mobile rendering clean)

**Defects captured (NOT implemented; for a follow-up phase):**
1. **P1 — ADR-079 has a hole**: the onboarder LLM can drop a detail-preferred URL entirely before save, leaving the save gate nothing to protect. Saw this on stress26-mx3s with B&H Photo (LLM omitted the URL after one failed probe and stuck a URL-less placeholder in `sources_pending`). Fix needs both a prompt rule + a deterministic save-time guard. Details in STRESS_TEST_26.md § Defect 1.
2. **P1 — ADR-084 callout fidelity bug**: the per-source `passed` count appears host-aggregated, so an `error: HTTPError ...` row on a host whose other URL succeeded sees `passed > 0` → classifier returns `OK` → no bullet. Defeats the "explain every 0" goal for non-skip errors. Saw on stress26-xm5 with Best Buy. Details in STRESS_TEST_26.md § Defect 2.
3. **P2 — `microcenter.com` `known_failure` registry entry is stale**: the Ryzen 7 9700X detail URL extracted cleanly (`1 fetched, 1 passed`, $279.99) at the new tier 3 + networkidle defaults. Registry should be re-probed (N≥3) and either removed or downgraded `blocker` → `warning`. Details in STRESS_TEST_26.md § Defect 3.

**Additional paper-cuts (P2/P3):** ddr5 onboarder emitted `spec_attrs` block without the required `required: <bool>` field (extra round-trip; same class as ADR-074 followup #2); `low_seller_feedback` flag renders as `(no description)` in the Flags section; Newegg search returned 820 KB body but 0 parsed listings (PARSER_GAP candidate — capture as fixture). All in STRESS_TEST_26.md § Defects 4–6.

## Next session — start here

**Next session is Phase 27 — Fix the 3 Phase 26 defects + live re-verify** (user-requested 2026-05-24). Full brief + per-defect fix recipe + stress27-* re-verification matrix in PHASES.md. This is a LIVE, costed (~$2–5) session: code fixes for D1+D2+D3, then re-run a smaller version of the Phase 26 sweep (`stress27-mx3s`, `stress27-xm5`, `stress27-mc`) to confirm each defect is actually resolved on production data. Deliverable is the fixes shipped + a brief "verification PASS" follow-up appended to STRESS_TEST_26.md (or sibling STRESS_TEST_27.md).

**Other standing candidates** (only if Phase 27 is deferred):

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
- **microcenter.com Cloudflare bypass** (vendor-level) — challenge page served at `min_tier: 3`; tier 4 → silent AlterLab failure (`body_len=0`). Now recorded as a `known_failure` in `worker/src/product_search/vendor_quirks.yaml` (the onboarder is told to route microcenter into `sources_pending`), but the underlying bypass is still UNSOLVED — needs deeper AlterLab tier investigation or an alternate anti-bot path.
- **bhphotovideo.com search-tile extraction mismatch** (vendor-level) — search page renders (200 KB, 24 product mentions) but `_extract_candidates` finds only 4 anchors. Worked around via `prefer_page_type: detail` in the registry (detail URLs route through the Tier 1.5 extractor), but the *search-tile* walker itself is still blind to B&H tiles — a `wait_for: <css-selector>` or B&H-specific extractor tweak would recover search coverage.

> Older "noticed but deferred" / open-questions / per-phase notes (Phase 10–12 era) live in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md); they were stale fossils, not live items.
