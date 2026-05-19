# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts, reopened/extended/re-closed 2026-05-17); **Phase 19** (universal adapter accuracy & vendor reach, 2026-05-17); **Phase 20** (reliable scheduling trigger — genuinely proven end-to-end 2026-05-18, ADR-052/054).
- **Queued (next phase):** **Phase 18 — Polish + second-product proof** ([PHASES.md](PHASES.md#phase-18--polish--second-product-proof-replaces-old-phase-12)).
- **Most recent work:** a run of 2026-05-18 inter-phase fixes (ADR-053→063) culminating in **ADR-063** (delete-product UX), then a **2026-05-18 docs/campground cleanup** (this block). All inter-phase, none a Phase-18 gate.

## Current state — 2026-05-18 (docs/campground cleanup)

### What shipped

- **PROGRESS.md split** — was 2621 lines / 259 KB (over the Read-tool limit, so SESSION_PROTOCOL step 1 was literally impossible). All historical dated blocks moved verbatim to [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md); this file is now the lean live status only.
- **SESSION_PROTOCOL.md** — codified a hard size cap on PROGRESS.md + an explicit archive-on-phase/inter-phase-close step.
- **DECISIONS.md** — prepended a one-line-per-ADR status index (skim in seconds; ADR bodies untouched, still immutable history).
- **scratch/** — removed 5 stale tracked experiment scripts + untracked `aufschnitt.html`/`__pycache__`; `scratch/` now gitignored.
- **promptText.ts churn root-caused** — `.gitattributes` + `sync-prompt.js` now pin LF so the generated file is byte-stable; the perpetual modified-but-uncommitted tree state is gone (no more "carried forward" note every session).

### Carry-over from ADR-063 (still the authoritative forward queue)

ADR-063 (delete-product: touch-reachable trigger + portaled modal + post-delete reload) is in-repo + committed (`fa03642`). **Open gap:** the real DELETE→reload path is unverified locally (`WEB_SHARED_SECRET` unset in dev → route 500s; a genuine delete commits to `origin/main`, destructive) — spot-check on the deployed app when convenient.

Also still queued: **REVIEW PROD TEST RESULTS** for the deployed Schedule&Alerts editor (ADR-059 `price_basis`, ADR-060 guided builder, ADR-061 cron-quote) — especially the never-Claude-verified **mobile (~390px)** popover layout (chrome-devtools was blocked by a locked browser profile; the one true open verification gap).

## Next session — start here

1. Get the user's **prod test results** for the Schedule&Alerts editor (ADR-059/060/061) and confirm the never-verified mobile (~390px) popover layout. If a defect surfaces it's almost certainly in `web/lib/schedule.ts` (round-trip/builder) or the editor JSX.
2. Spot-check the real delete→reload on the deployed app (ADR-063).
3. Then start **Phase 18 — Polish + second-product proof**. (Profiles `lululemon-never-lost-keychain-wordmark` + `breville-barista-express` now exist on origin from the user's prod testing.)

## Blockers

None. (CI is green again — ADR-062 decoupled the worker suite + `validate-profiles` from app-mutable `products/`.)

## Noticed but deferred (live only)

- **ADR-040 auto-demote implementation** — `source_runs` table + streak prune in `_cmd_search`. Deferred by ADR-040 itself; until it lands, 5 dead bose `universal_ai` URLs sit in active `sources` (~$0.011/run; manual demote is the stopgap).
- **ADR-053 deferred items #1–#3** — robust source-error handling / "N source(s) errored" surfacing (now also relevant to `while_below` + `total`-basis zero-listing skips).
- **Scheduled tick #25 (06:11Z) failure-with-nothing-due** — investigate when convenient.
- **No in-app signal for silent external-trigger death** — a redundant independent trigger (e.g. Cloudflare Workers cron) is the only true fix; user was offered it, deferred.
- **Email-on-alert** — deferred to its own ADR + sign-off (push delivery is confirmed working in prod, ADR-057).
- **Phase 5 benchmark fixtures vs live data** — synth picks not re-confirmed against live `anthropic/claude-haiku-4-5` payloads (per ADR-019; not blocking, live data proves Haiku works).

> Older "noticed but deferred" / open-questions / per-phase notes (Phase 10–12 era) live in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md); they were stale fossils, not live items.
