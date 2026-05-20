# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts, reopened/extended/re-closed 2026-05-17); **Phase 19** (universal adapter accuracy & vendor reach, 2026-05-17); **Phase 20** (reliable scheduling trigger — genuinely proven end-to-end 2026-05-18, ADR-052/054).
- **Queued (next phase):** **Phase 18 — Polish + second-product proof** ([PHASES.md](PHASES.md#phase-18--polish--second-product-proof-replaces-old-phase-12)).
- **Most recent work:** a run of 2026-05-18 inter-phase fixes (ADR-053→063) culminating in **ADR-063** (delete-product UX), then the **2026-05-20 AlterLab custom parameters** (ADR-065). All inter-phase, none a Phase-18 gate.

## Current state — 2026-05-20 (AlterLab custom parameters for bot-block avoidance — ADR-065, DONE)

### What shipped (ADR-065; this session; on `origin/main` after push)

- **Adapter custom parameters** — Added `alterlab_options` propagation to `universal_ai.py` fetch cascade. Extracts `country`, `min_tier`, and `wait_for` from profile `sources` configuration (under `query.extra`) and serializes them in the AlterLab POST API payload.
- **CLI custom parameters** — Exposed `--country`, `--min-tier <int>`, and `--wait-for` parameters in the CLI's `probe-url` diagnostic utility, enforcing validation against `ALTERLAB_API_KEY`.
- **Anti-fragile protection** — Preserved backward compatibility for positional lambda mock definitions in all existing tests by only passing `alterlab_options` keyword-arguments when non-empty.
- **Verification & Tests** — Added comprehensive unit tests in `test_universal_ai.py` and `test_cli.py`. The entire test suite (262 tests) is 100% green. Manually probed Best Buy successfully using `--country us --min-tier 3`.
- **ADR-065** — Added decisions log for custom AlterLab parameter mapping inside `docs/DECISIONS.md`.

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
