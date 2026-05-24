# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts); **Phase 19** (universal adapter accuracy & vendor reach); **Phase 20** (reliable scheduling trigger); **Phase 22 — Recall reliability under degraded AlterLab + onboarder robustness** (ADR-078/079/080, 2026-05-24); **Phase 23 — Hybrid filter restoration + headless E2E verification** (both Parts A and B closed, 2026-05-24); **Phase 24 — Vendor-quirks coverage audit + Amazon JS-render fix** (ADR-082, 2026-05-24).
- **Queued next:** **Phase 18 — Polish + second-product proof**.
- **Most recent work:** 2026-05-24 **Phase 24 closed.** `vendor_quirks.yaml`: amazon.com + backmarket.com got `default_alterlab_options: {country: us, min_tier: 3, wait_condition: networkidle}` (Adorama probe showed bare path already returns 23 JSON-LD products — no defaults added). Registry-load consistency check (`_check_alterlab_known_good_consistency`) warns on `alterlab_known_good: true` without defaults — already caught 3 latent gaps (centralcomputer/ebay/serversupply). CLI `probe-url` now mirrors the adapter's `merge_alterlab_options` so the diagnostic matches runtime. Amazon search fixture captured (1.45 MB through AlterLab tier-3+networkidle) + regression test. 314/314 worker tests pass; web tsc/lint/parity/guards/build green. Committed & pushed.

## Current state — 2026-05-24 Phase 24 closed

**Worker (`worker/src/product_search/vendor_quirks.{yaml,py}`, `cli.py`)**:
- `amazon.com` + `backmarket.com` in the registry now carry `default_alterlab_options: {country: us, min_tier: 3, wait_condition: networkidle}`. `adorama.com` left alone — its bare-path probe (curl_cffi fallback) returned 391 KB body with **23 JSON-LD listings including MX Master 3S at $119.99**; adding AlterLab defaults would burn cost for no recall gain. Probe evidence + decision pinned in test_vendor_quirks.py.
- `_check_alterlab_known_good_consistency` runs every `_load_registry()` call: WARNs naming any host with `alterlab_known_good: true` and no `default_alterlab_options`. Three pre-existing gaps surfaced (`centralcomputer.com`, `ebay.com`, `serversupply.com`) — queued in the follow-up list below.
- `_cmd_probe_url` now calls `merge_alterlab_options(url, cli_supplied_opts)` before fetching, so `cli probe-url <amazon-url>` (no flags) prints `applying vendor_quirks defaults: {country: us, min_tier: 3, wait_condition: networkidle, render_js: True}` and uses the same path the worker would. Existing CLI tests unaffected (their fixture URLs use unknown hosts → merge no-ops).

**Fixture + test guard** (`worker/tests/fixtures/universal_ai/amazon_search_logitech_mx_master_3s.html`, 1.45 MB):
- Captured this session through AlterLab at the new defaults. New `test_amazon_search_fixture_extracts_dp_candidates_with_prices` extracts 19 `/dp/` candidates (16 with prices); asserts ≥5 with prices and that the target MX Master 3S is in the set. Regression-frozen.

**Tests** (314/314 pass, ruff/mypy clean on touched files; web `tsc`/`lint`/`test:parity` 2/2/`test:guards` 6/6/`next build` all green after `sync-prompt.js` regen of `promptText.ts` + `vendor-quirks-data.ts`):
- 6 new in `test_vendor_quirks.py` (amazon merge through committed registry; source override wins; backmarket merge; adorama no-defaults pin; caplog positive + negative).
- 1 new in `test_universal_ai.py` (amazon fixture extraction guard).

**Live runtime-path validation**: one `cli probe-url https://www.amazon.com/s?k=logitech+mx+master+3s` (no flags) at session end confirmed the merge fires through the runtime path (printed `applying vendor_quirks defaults: {...render_js: True}`). The actual fetch fell through to curl_cffi because AlterLab is presently in `browser_pool_exhausted` 422 (upstream transient called out as out-of-scope in the Phase 24 brief — ADR-078's circuit breaker handles it). The successful tier-3+networkidle fetch earlier in this session is the saved fixture (1.44 MB / 42 anchors / 16 dp anchors with prices), which is the regression evidence.

## Next session — start here

**Top priority candidates** (pick one; no one phase is uniquely blocking):

1. **Phase 24 follow-up — same-class audit for the 3 hosts the new consistency check flagged.** `centralcomputer.com`, `ebay.com`, `serversupply.com` all have `alterlab_known_good: true` without `default_alterlab_options` and now log a WARNING at every registry load. eBay has its own dedicated adapter (probably benign — `universal_ai_search` rarely routes to ebay.com); the other two are universal_ai-only and likely need the same Amazon-class fix. Repeat the Phase 24 probe-and-add pattern. ~$0.003 in probes.
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
