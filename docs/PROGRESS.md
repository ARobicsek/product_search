# Progress

**This is the live status file. Every dev session reads it first and updates it last.**
Keep it small (see [SESSION_PROTOCOL.md](SESSION_PROTOCOL.md) — hard cap, archive-on-close).
Full session-by-session history → [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md) (append-only, NOT read at session start).

## Active phase

- **Closed:** Phases 0–16; **Phase 17** (schedule editor + alerts, reopened/extended/re-closed 2026-05-17); **Phase 19** (universal adapter accuracy & vendor reach, 2026-05-17); **Phase 20** (reliable scheduling trigger — genuinely proven end-to-end 2026-05-18, ADR-052/054).
- **Queued (next phase):** **Phase 18 — Polish + second-product proof** ([PHASES.md](PHASES.md#phase-18--polish--second-product-proof-replaces-old-phase-12)).
- **Most recent work:** 2026-05-20 **ADR-068 — vendor quirks registry** (system hardening: one source of truth for per-vendor knowledge, consumed by adapter + prompt + save-time gate). Preceded by ADR-065 (AlterLab custom params), ADR-067 (onboarder redundant detail-URL backup), profile-URL fixes for sony-wh-1000xm5, accessory-bundle `pack_size` guard. Full detail in [PROGRESS_ARCHIVE.md](PROGRESS_ARCHIVE.md).

## Current state — 2026-05-20 ADR-068 vendor quirks registry built locally; needs commit + prod validation

This session diagnosed why the user's sony-wh-1000xm5 re-onboard dropped Best Buy and only half-followed ADR-067, then built **ADR-068** to harden the system. **Root cause:** vendor knowledge lived in three uncoordinated places (prompt, per-profile YAML, probe-url allowlist) with no path from "learned a quirk" to "system uses it" — so e93fd47's Best Buy `&intl=nosplash` fix never reached the onboarder.

What shipped this session (local; **not yet committed/pushed** at time of writing):
- **`worker/src/product_search/vendor_quirks.yaml`** — single source of truth, keyed by www-stripped host. Fields: `default_alterlab_options`, `url_transforms`, `force_detail_backup`, `alterlab_known_good`, `prefer_page_type`, `known_failure`, `notes`. Seeded with bestbuy (nosplash transform + defaults), target, microcenter (known_failure), bhphotovideo (prefer detail), + amazon/walmart/ebay/backmarket/etc known-good.
- **`worker/src/product_search/vendor_quirks.py`** + `tests/test_vendor_quirks.py` (11 tests) — loader: `merge_alterlab_options` (source wins), `apply_url_transforms`.
- **`adapters/universal_ai.py`** — `fetch()` now merges registry defaults + applies URL transforms before fetch, logs applied transforms, honors `extra.skip_vendor_quirks`. +5 integration tests. **Old profiles benefit automatically.**
- **`web/scripts/sync-prompt.js`** — now also reads the YAML, injects rendered quirks into the prompt between `<!-- VENDOR_QUIRKS_BEGIN/END -->` markers, and emits `web/lib/onboard/vendor-quirks-data.ts` (FORCE_DETAIL_BACKUP_HOSTS + ALTERLAB_KNOWN_GOOD_HOSTS).
- **`web/lib/onboard/probe-url.ts`** — known-good allowlist now imported from generated registry data (www-stripped lookup).
- **`web/lib/onboard/adr067-check.ts`** + save route + OnboardChat UI — save-time SOFT warning when a `force_detail_backup` vendor lacks a search-or-detail URL; surfaced as an amber panel; auto-redirect suppressed when warnings present.

Worker suite 280/280 green; web `tsc --noEmit` + eslint clean.

## Next session — start here

1. **Commit + push this session's work if not already done** (check `git log origin/main..HEAD`). Then **validate ADR-068 in prod**: re-onboard a single-SKU product on Best Buy + Target and confirm (a) the onboarder's chat reflects the rendered vendor knowledge (mentions ADR-067 dual URLs, skips microcenter into sources_pending), (b) the save-time amber warning fires if it adds only one URL for Target/Best Buy, (c) a scheduled run shows Best Buy actually returning listings (the nosplash transform applied — grep worker logs for `applied` / `vendor_quirks`).
2. **UI not yet browser-verified:** the OnboardChat amber warnings panel was typechecked but not exercised end-to-end (needs a live onboard reaching save with a force_detail_backup violation). Verify at ~390px mobile width per the mobile-layout rule.
3. **Then** the prior queue still applies: prod test results for Schedule&Alerts editor (ADR-059/060/061) + mobile (~390px) popover layout verification; delete→reload spot check (ADR-063); then start **Phase 18 — Polish + second-product proof**.

## Blockers

None. CI is green (ADR-062 decoupled the worker suite + `validate-profiles` from app-mutable `products/`). Worker test suite ran 264/264 green at end of 2026-05-20 session.

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
