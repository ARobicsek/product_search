# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts, reopened/extended/re-closed 2026-05-17); **Phase 19** (universal adapter accuracy & vendor reach, 2026-05-17); **Phase 20** (reliable scheduling trigger — genuinely proven end-to-end 2026-05-18, ADR-052/054).
- **Queued (next phase):** **Phase 18 — Polish + second-product proof** ([PHASES.md](PHASES.md#phase-18--polish--second-product-proof-replaces-old-phase-12)).
- **Most recent work:** a run of 2026-05-18 inter-phase fixes (ADR-053→063) culminating in **ADR-063** (delete-product UX), then the **2026-05-20 AlterLab custom parameters** (ADR-065), then a same-day **sony-wh-1000xm5 vendor-URL unblock** pass (target typo + bestbuy nosplash; microcenter/bhpv deferred). All inter-phase, none a Phase-18 gate.

## Current state — 2026-05-20 (AlterLab custom parameters — ADR-065 DONE; sony-wh-1000xm5 vendor-URL follow-up DONE)

### What shipped earlier today (ADR-065; on `origin/main`)

- **Adapter custom parameters** — Added `alterlab_options` propagation to `universal_ai.py` fetch cascade. Extracts `country`, `min_tier`, and `wait_for` from profile `sources` configuration (under `query.extra`) and serializes them in the AlterLab POST API payload.
- **CLI custom parameters** — Exposed `--country`, `--min-tier <int>`, and `--wait-for` parameters in the CLI's `probe-url` diagnostic utility, enforcing validation against `ALTERLAB_API_KEY`.
- **Anti-fragile protection** — Preserved backward compatibility for positional lambda mock definitions in all existing tests by only passing `alterlab_options` keyword-arguments when non-empty.
- **Verification & Tests** — Added comprehensive unit tests in `test_universal_ai.py` and `test_cli.py`. The entire test suite (262 tests) is 100% green. Manually probed Best Buy successfully using `--country us --min-tier 3`.
- **ADR-065** — Added decisions log for custom AlterLab parameter mapping inside `docs/DECISIONS.md`.

### Follow-up (this session — vendor-URL fixes on `origin/main` after push)

The first scheduled run after ADR-065 deployed still returned 0 listings passed for `sony-wh-1000xm5` (target.com 37/0; microcenter/bhpv/bestbuy all 0/0). Live probing with the profile's exact `alterlab_options` showed **`alterlab_options` propagation itself works correctly** — three different vendor-side failures. Two fixed at the profile-URL level:

- **target.com**: URL typo `sony+wh1000mx5` (m/x swapped) → `sony+wh1000xm5`. Caused unreliable fuzz matches across runs; an earlier same-day run got 43/1 by luck, the prod run got 37/0.
- **bestbuy.com**: Best Buy serves a country-selector splash on first visit *despite* `country: us` AlterLab routing — fixed by appending `&intl=nosplash`. Verified live (body 7 KB → 1.6 MB; anchor candidates 0 → 12).

Two deferred (see "Noticed but deferred"):

- **microcenter.com**: AlterLab returns a Cloudflare challenge page even at `min_tier: 3`; `min_tier: 4` silently fails (body_len 0).
- **bhphotovideo.com**: page renders fully (200 KB body, 24 WH-1000XM5 mentions, prices visible) but `_extract_candidates` finds only 4 anchors — search-result tiles aren't in the static-HTML shape the walker recognises.

Profile change: only [products/sony-wh-1000xm5/profile.yaml](../products/sony-wh-1000xm5/profile.yaml) — 2 URL edits. No code changes, no new ADR (tactical per-profile fix).

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
- **`sony-wh-1000xm5` microcenter.com Cloudflare bypass** — challenge page served, not solved at `min_tier: 3`; bumping to tier 4 produces silent AlterLab failure (`body_len=0`). Needs deeper AlterLab tier investigation or alternate anti-bot path.
- **`sony-wh-1000xm5` bhphotovideo.com structural extraction mismatch** — full page renders but anchor walker finds only 4 candidates. Likely needs a `wait_for: <css-selector>` against the tile container, or a B&H-specific extractor tweak.

> Older "noticed but deferred" / open-questions / per-phase notes (Phase 10–12 era) live in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md); they were stale fossils, not live items.
