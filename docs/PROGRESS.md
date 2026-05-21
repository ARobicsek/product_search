# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts, reopened/extended/re-closed 2026-05-17); **Phase 19** (universal adapter accuracy & vendor reach, 2026-05-17); **Phase 20** (reliable scheduling trigger — genuinely proven end-to-end 2026-05-18, ADR-052/054).
- **Queued (next phase):** **Phase 18 — Polish + second-product proof** ([PHASES.md](PHASES.md#phase-18--polish--second-product-proof-replaces-old-phase-12)).
- **Most recent work:** 2026-05-20 **ADR-068 — vendor quirks registry** (system hardening: one source of truth for per-vendor knowledge, consumed by adapter + prompt + save-time gate); **prod-validated 2026-05-21** (one follow-up bug found — see below). Preceded by ADR-065 (AlterLab custom params), ADR-067 (onboarder redundant detail-URL backup), profile-URL fixes for sony-wh-1000xm5, accessory-bundle `pack_size` guard. Full detail in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md).

## Current state — 2026-05-21 ADR-068 shipped (`e0db48b`) + validated in prod; one follow-up bug found

ADR-068 (vendor quirks registry) is committed/pushed and **validated by a live prod re-onboard of sony-wh-1000xm5 on 2026-05-21**. All three registry behaviors propagated to the onboarder and the deterministic guardrail fired:
- microcenter `known_failure` → onboarder proactively warned + routed to `sources_pending` (last time it falsely promised listings);
- B&H `prefer_page_type: detail` → onboarder tried the detail URL first;
- Best Buy is alive again (probe 1.6 MB / 6 anchors vs the prior 7 KB / 0); the saved search URL's path is `/site/searchpage.jsp` so the runtime adapter auto-appends `&intl=nosplash`;
- **ADR-067 save-time amber warning fired for Target AND Best Buy** (LLM still added only search URLs — the prompt half of ADR-067 didn't take, exactly the LLM drift the deterministic guardrail exists to catch; it caught it).

(ADR-068 build details archived — see commit `e0db48b` and [DECISIONS.md ADR-068](DECISIONS.md).)

### NEW BUG found during that validation — probe under-tests `page_type: "detail"` URLs (the next task)

During the same run, B&H's **detail** URL was demoted to `sources_pending` because the probe reported **0 anchors / 0 JSON-LD**. That is a **false negative**: a detail page legitimately has ~0 list anchors, and its price often lives only in DOM text (not JSON-LD). Confirmed root cause:
- The chat-time `probe_url` tool ([web/app/api/onboard/chat/route.ts](../web/app/api/onboard/chat/route.ts) ~L148-240) takes only `url` + `alterlab_options` — **no `page_type`** — and calls `probeUrl()`, which only does JSON-LD extraction + `countProductAnchors()`. It returns `{anchorCount: 0, jsonldCount: 0}` for a perfectly-good detail page, and the **onboarder LLM interprets that as "can't extract" and demotes the vendor**.
- The thing the probe SHOULD model for a detail URL is the runtime **Tier 1.5 extractor** ([universal_ai.py](../worker/src/product_search/adapters/universal_ai.py) `_extract_detail_listing`, ~L1431): one `claude-haiku-4-5` call on the stripped page text that pulls the single product's price (URL is always the source URL, never LLM-produced). The TS probe does none of this.
- **NOT the culprit:** the background save-time gate (`gate-universal-ai.ts`). `probeUrl()` returns `ok:true` for any 2xx with body ≥ 500 regardless of anchor count, and the gate only demotes on `ok:false` — so detail URLs added to a profile *survive* the background gate. Don't waste time there.

Why it matters: this same false-negative will hit the **ADR-067 detail-URL backups** we now want the onboarder to add for Target/Best Buy — they'd look "unextractable" to the probe and get talked into `sources_pending`, partly defeating both ADRs.

## Next session — start here

1. **Fix the detail-URL probe gap.** Make the probe evaluate `page_type: "detail"` URLs by detail-extractability, not list-anchor count. Steps:
   - Add a `page_type` (`"search"`|`"detail"`) field to the `probe_url` tool input schema in [chat/route.ts](../web/app/api/onboard/chat/route.ts) and plumb it through to `probeUrl()`; also pass `page_type` from the draft source in [gate-universal-ai.ts](../web/lib/onboard/gate-universal-ai.ts) for consistency.
   - In [probe-url.ts](../web/lib/onboard/probe-url.ts), when `page_type === "detail"`: stop gating/reporting on `anchorCount`. Instead report a `detailExtractable` signal. **Design decision to make:** cheap heuristic (rendered 200 + a `$X.XX` price token + product-ish `<title>`) vs. faithful mirror of Tier 1.5 (one Haiku call on stripped text). The cheap path risks false positives from "related items" prices; the Haiku path is faithful but costs an LLM call per detail probe. Recommend the Haiku mirror for fidelity (probes already spend AlterLab budget; one Haiku call is cheap) — but confirm with the user first.
   - Update the onboarder prompt ([onboard_v1.txt](../worker/src/product_search/onboarding/prompts/onboard_v1.txt)) so the LLM knows: for a detail URL, 0 anchors is EXPECTED — judge by the `detailExtractable` signal, not anchors. Regenerate via `node web/scripts/sync-prompt.js`.
   - Re-onboard sony-wh-1000xm5 to confirm B&H's detail URL is now KEPT, and that adding Target/Best Buy detail backups (per the ADR-067 warning) survives the probe.
2. **(Optional, lower priority)** The onboarder LLM still won't proactively add the ADR-067 detail URLs even though the prompt + `force_detail_backup` tell it to. The save-time guardrail catches it, so this is a nicety, not a gate. Consider either a firmer prompt or having the chat offer to auto-add the detail URL when the warning triggers.
3. **UI not yet browser-verified:** the OnboardChat amber warnings panel rendered correctly in this prod run (warning text appeared as designed) but was NOT checked at ~390px mobile width — verify per the mobile-layout rule.
4. **Then** the prior queue still applies: prod test results for Schedule&Alerts editor (ADR-059/060/061) + mobile (~390px) popover layout verification; delete→reload spot check (ADR-063); then start **Phase 18 — Polish + second-product proof**.

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
