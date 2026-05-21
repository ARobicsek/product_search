# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts, reopened/extended/re-closed 2026-05-17); **Phase 19** (universal adapter accuracy & vendor reach, 2026-05-17); **Phase 20** (reliable scheduling trigger — genuinely proven end-to-end 2026-05-18, ADR-052/054).
- **Queued (next phase):** **Phase 18 — Polish + second-product proof** ([PHASES.md](PHASES.md#phase-18--polish--second-product-proof-replaces-old-phase-12)).
- **Most recent work:** 2026-05-21 **ADR-070 — probe Tier 1.5 mirror made faithful**: while verifying ADR-069 live, found the TS probe's AlterLab fetch omitted `asp:true` (the runtime always sends it), so AlterLab returned degraded/Cloudflare-challenge renders and `detailExtractable` was a false negative for a valid Target detail URL the runtime extracts cleanly. Added `asp:true`; confirmed Target detail now `detailExtractable:true` ($249.99). Preceded by ADR-069 (detail-URL probe), ADR-068 (vendor quirks registry), ADR-065/-067. Full detail in [DECISIONS.md](DECISIONS.md) + [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md).

## Current state — 2026-05-21 ADR-070 implemented + ADR-069 partially verified live (build green, pushed)

ADR-069 was verified against the live Sony WH-1000XM5 detail URLs by running the **deployed** `probeUrl()` (via a temporary local Next route, since `probe-url.ts` is `server-only` and there is no web test harness) against B&H + Target with the registry's AlterLab options, and cross-checking with the **runtime** `cli probe-url --render --detail`. Findings:
- **The ADR-069 logic is correct** (Tier 1.5 mirror + ADR-001 verbatim-price guard never hallucinated; it correctly refused the bot-challenge pages).
- **But the mirror was unfaithful at the fetch layer (ADR-070):** the TS `fetchViaAlterlab` omitted `asp:true`. Same Target URL, same `country:us/min_tier:3/render_js`, differing only by `asp`: TS got a 380 KB partial ("temporary issue", no price) → `detailExtractable:false`; runtime got 1.58 MB → extracted `$249.99`. The probe would have wrongly told the onboarder to demote a Target detail URL — re-introducing the false-negative ADR-069 set out to kill. **Fixed**: added `asp:true`; a fresh Target detail URL then returned `detailExtractable:true` ($249.99) via the deployed code path.
- **B&H is a separate, still-open vendor-reach failure:** even the runtime path returned an empty body (status 0) for B&H's WH-1000XM5 detail URL this session; the probe got a Cloudflare "Just a moment…" challenge. B&H detail is NOT reliably extractable in either path right now, so keeping B&H in `sources_pending` (as the committed profile does) remains correct. Likely AlterLab-vs-B&H-Cloudflare, possibly compounded by the `wait_for` int-seconds-vs-CSS-selector ambiguity.

Changed this session: [probe-url.ts](../web/lib/onboard/probe-url.ts) (`asp:true` in the AlterLab request body). `tsc --noEmit` clean, `eslint` 0 errors, `next build` green. No runtime/worker code changed.

## Next session — start here

1. **Finish the ADR-069 live verification through the actual onboarder LLM** (this session verified the probe *mechanism* directly, not the chat path). Re-onboard sony-wh-1000xm5 in prod and watch that the onboarder (a) passes `page_type:"detail"` to `probe_url`, (b) now KEEPS a Target (and any other asp-needing) detail backup because the probe returns `detailExtractable:true`, and (c) still correctly routes B&H to `sources_pending` (B&H detail genuinely doesn't extract — see below). The `asp:true` fix is on `origin/main`; confirm Vercel redeployed it before testing.
2. **B&H detail vendor-reach (NEW, deferred):** B&H's WH-1000XM5 detail URL returns empty (runtime) / Cloudflare challenge (probe) even at `min_tier:3, asp:true`. Investigate: (a) the `wait_for` type ambiguity — registry has `wait_for:5` (int seconds) but `cli --wait-for`/AlterLab may treat it as a CSS selector; (b) whether a different tier/wait clears B&H's Cloudflare. Until solved, B&H stays in `sources_pending`. Consider recording it as a `known_failure` in `vendor_quirks.yaml` like microcenter so the onboarder is told to park it.
3. **(Optional, lower priority)** The onboarder LLM still may not proactively add the ADR-067 detail URLs even though the prompt + `force_detail_backup` tell it to. The save-time guardrail catches it, so this is a nicety, not a gate.
4. **UI not yet browser-verified:** the OnboardChat amber warnings panel was NOT checked at ~390px mobile width — verify per the mobile-layout rule.
5. **Then** the prior queue still applies: prod test results for Schedule&Alerts editor (ADR-059/060/061) + mobile (~390px) popover layout verification; delete→reload spot check (ADR-063); then start **Phase 18 — Polish + second-product proof**.

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
