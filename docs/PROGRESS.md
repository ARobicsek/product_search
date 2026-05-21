# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts, reopened/extended/re-closed 2026-05-17); **Phase 19** (universal adapter accuracy & vendor reach, 2026-05-17); **Phase 20** (reliable scheduling trigger — genuinely proven end-to-end 2026-05-18, ADR-052/054).
- **Queued (next phase):** **Phase 18 — Polish + second-product proof** ([PHASES.md](PHASES.md#phase-18--polish--second-product-proof-replaces-old-phase-12)).
- **Most recent work:** 2026-05-21 **ADR-069 — detail-URL probe gap fixed** (probe now judges `page_type:"detail"` URLs by a faithful Tier 1.5 mirror → `detailExtractable`, not list-anchor count; ends false demotion of valid detail pages). Preceded by ADR-068 (vendor quirks registry, prod-validated 2026-05-21), ADR-065/-067, sony-wh-1000xm5 fixes. Full detail in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md).

## Current state — 2026-05-21 ADR-069 implemented (local build green, prod re-onboard pending)

The detail-URL probe false-negative (B&H detail URL demoted on 0 anchors, found during the ADR-068 prod validation) is fixed. `probe_url` now takes `page_type` end-to-end; for `page_type:"detail"` it reports `detailExtractable` from a faithful TS port of the runtime Tier 1.5 extractor (strip-to-text → one `claude-haiku-4-5` call → verbatim price re-verify, ADR-001) instead of gating on anchors. `ok`/save-time-gate semantics are unchanged (still hard-failure-only, ADR-038); `detailExtractable` is a diagnostic signal the onboarder prompt now tells the LLM to judge detail URLs by. See [DECISIONS.md ADR-069](DECISIONS.md).

Changed: [probe-url.ts](../web/lib/onboard/probe-url.ts) (Tier 1.5 mirror + `page_type` param + `detailExtractable`), [chat/route.ts](../web/app/api/onboard/chat/route.ts) (`page_type` tool field), [gate-universal-ai.ts](../web/lib/onboard/gate-universal-ai.ts) (`page_type` from `extra.page_type`, `detailExtractable` in report), [onboard_v1.txt](../worker/src/product_search/onboarding/prompts/onboard_v1.txt) (+ regenerated `promptText.ts`). Verified locally: `tsc --noEmit` clean, `eslint` clean, `next build` green. **No runtime adapter code changed** — the worker Tier 1.5 path it mirrors is already covered by `test_universal_ai.py` detail tests.

## Next session — start here

1. **Verify ADR-069 in prod** (the implementation is on `origin/main` but NOT yet exercised live): re-onboard sony-wh-1000xm5 and confirm (a) B&H's `page_type:"detail"` URL is now KEPT (probe returns `detailExtractable: true`), and (b) when you add Target/Best Buy detail backups per the ADR-067 amber warning, they survive the probe rather than getting talked into `sources_pending`. Watch the onboarder actually pass `page_type` to `probe_url`.
2. **(Optional, lower priority)** The onboarder LLM still won't proactively add the ADR-067 detail URLs even though the prompt + `force_detail_backup` tell it to. The save-time guardrail catches it, so this is a nicety, not a gate. Consider either a firmer prompt or having the chat offer to auto-add the detail URL when the warning triggers.
3. **UI not yet browser-verified:** the OnboardChat amber warnings panel rendered correctly in the 2026-05-21 prod run (warning text appeared as designed) but was NOT checked at ~390px mobile width — verify per the mobile-layout rule.
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
