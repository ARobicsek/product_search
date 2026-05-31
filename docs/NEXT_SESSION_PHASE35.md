# Next session — Phase 35 (alerts `new_vendor_carries` + verify schedule/push on the v2 pipeline)

**You are a fresh session. Follow this brief literally and in order. Do not improvise scope.**
Read these first, in this order, and nothing else until told:
1. `docs/PROGRESS.md` — "Most recent work" + "Current state" + "Blockers".
2. This file (top to bottom).
3. `docs/REBUILD_PLAN.md` §5 (run pipeline steps 7 + 9), §8 (error taxonomy), §10 (Phase 35 row).
4. `docs/DECISIONS.md` — skim ADR-137 (what Phase 34 did), ADR-133 (the rebuild plan), ADR-057 (push).

**Branch:** `main`. **One phase per session — do Phase 35 only.** Push is pre-authorized (CLAUDE.md); never `--no-verify`.

---

## 0. Pre-flight (run these, confirm green, then stop and read)
```
cd /c/dev/product-search
git fetch origin && git status            # expect clean / in sync; if behind, git pull --rebase origin main
cd web && npm install                      # if node_modules missing
```
Baseline you must NOT regress:
- Worker: `cd /c/dev/product-search/worker && python -m pytest -q` → **495 passed** (use the project's normal test runner; if `uv` is set up, `uv run pytest -q`).
- Web: `cd /c/dev/product-search/web && npm run test:guards` (74/74) + `npm run test:parity` (6/6).

`SERPER_API_KEY` is already in `web/.env.local` and root `.env` (validated). `EBAY_CLIENT_ID/SECRET` are in root `.env` too.

---

## TASK 0 — Live smoke test FIRST (≈15 min; do before any code)

Phase 34 wired the v2 onboarder but its live happy-path was never run with a real Serper key. Now it can be. **Goal: confirm onboarder → save → v2 run → report works end-to-end with live data.**

How to drive the UI locally (the chrome-devtools MCP is container-pinned and will NOT work on this Windows box — see the memory note `local-browser-verification`):
1. Start the dev server FROM the web dir: `cd /c/dev/product-search/web && npm run dev` (background). Wait for "Ready". (Running it from the repo root fails — root has no package.json.)
2. `npm i --no-save playwright-core` (keeps package.json clean). Launch the SessionStart Chromium at `C:/Users/ariro/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe`, viewport 375×812, navigate to `http://localhost:3000/onboard`.
3. Send a real product request (e.g. "Track the DJI Neo 2 Motion Fly More Combo drone, new only, in stock, include eBay"). Confirm: the assistant builds a v2 draft, calls `serper_preview`, and **real Google-Shopping offers now render** in the right-pane panel (NOT the "SERPER_API_KEY not configured" amber state). Screenshot at 375px; confirm no horizontal overflow.
4. **DECISION POINT — saving commits to `origin/main` via the app's GitHub flow** (CLAUDE.md: tests must never depend on a live product; the app rewrites/deletes `products/<slug>/`). Pick ONE:
   - (a) Use a clearly disposable slug, click Save, then **run it** with the worker: `cd worker && python -m product_search.cli search <slug>` (it routes schema_version:2 → `run_v2`). Confirm the report renders ranked offers. Then **delete the test product** (remove `products/<slug>/`, commit the deletion) so it doesn't pollute prod.
   - (b) Stop before Save; instead copy the streamed draft into a committed v2 fixture under `worker/tests/fixtures/profiles_v2/` and run `run_v2` against it via `PRODUCT_SEARCH_PRODUCTS_DIR` (ADR-062 pattern). No origin pollution.
   - **Recommended: (a)** with a throwaway slug if the owner is OK with one short-lived test product; otherwise (b).
5. Clean up: kill the dev server, delete any temp script/screenshots, `npm uninstall --no-save playwright-core`.

Record what you saw in PROGRESS.md (recall worked / didn't, any defects). If a real defect appears, note it as "noticed but deferred" — do NOT fix it unless it blocks Phase 35.

---

## TASK 1 — Implement `new_vendor_carries` for the v2 run pipeline (the core of Phase 35)

### What already exists (READ these before writing anything — do not reinvent)
- `worker/src/product_search/alerts.py` — the Phase-17 alerts evaluator, **reused by v2**:
  - `evaluate_alerts(rules, current, previous, state) -> list[FiredAlert]`.
  - Rule types today: `PriceBelowAlert` (modes `is_below`/`while_below`/first-ever) and **`VendorSeenAlert`** (`_evaluate_vendor_seen`, line ~218). **IMPORTANT:** `VendorSeenAlert` is *per-specific-host* ("did host X start carrying", `_stable_key` = `vendor_seen|{rule.host}`) and its headline is "<target> now has at least one passing listing". The v2 plan's `new_vendor_carries: true` is **host-agnostic**: "did ANY new host appear in today's survivors that wasn't in yesterday's". So you most likely need a **new rule type** (e.g. `NewVendorCarriesAlert`) — confirm by reading `_evaluate_vendor_seen`; reuse it only if it genuinely covers any-new-host.
  - `load_previous_run(slug, exclude=csv_path)` → previous run's `list[Listing]` (or None). `load_alerts_state` / `save_alerts_state` for armed-state rules.
- `worker/src/product_search/storage/diff.py` — `diff_snapshots(previous, current)` already computes new/dropped/changed. Check whether it surfaces **new hosts** (the host of a survivor not present in `previous`); reuse it for the new-vendor diff instead of writing a fresh host-diff.
- `worker/src/product_search/cli.py` lines ~555–685 — **the exact template for wiring diff + alerts + push.** It: computes `diff_snapshots(previous, current)`, calls `notify_material_change(slug, headline)` for new listings/price drops, then `if profile.alerts:` runs `load_previous_run` → `load_alerts_state` → `evaluate_alerts(...)` → `save_alerts_state` → `notify_material_change(slug, fa.headline)`. **The v2 run path does NOT do any of this yet.**
- `worker/src/product_search/run_v2.py` — line ~19 literally says "Alerts (`new_vendor_carries`) remain a Phase 35 concern." This is where (or near where) you add the diff/alerts/push step for v2.
- `worker/src/product_search/profile_v2.py` line ~156 — `alerts: list[dict[str, Any]]` is **untyped/permissive** with a comment: "Phase 35 types the rule set (incl. the new `new_vendor_carries` rule)." You must type it.
- `worker/src/product_search/notify.py` — `notify_material_change(slug, headline)` is the iPhone push (ADR-057). Read its signature.

### Sub-steps (do in order; commit nothing until the verification checklist passes)
1. **Type the v2 alert rules.** In `profile_v2.py`, replace the permissive `alerts: list[dict]` with a typed discriminated union that accepts (a) the carried-over Phase-17 price rules and (b) the new `new_vendor_carries` rule. Mirror how v1 `profile.py` types its `AlertRule` union (read it). Keep it backward-compatible with what the web schedule/alerts editor writes (see Task 2 — check the editor's YAML shape first so the types match).
2. **Implement the evaluator** for `new_vendor_carries`: deterministic (no LLM — ADR-001). Compute the set of survivor hosts this run minus the set from `previous` (use `load_previous_run` + `diff_snapshots` if it already gives new hosts). Fire one `FiredAlert` per new host (or one aggregated alert listing them) with a clear headline like "New vendor now carries <target>: <host>". First-run (`previous is None`) policy: match `VendorSeenAlert`'s convention (read `_evaluate_vendor_seen` — likely "first observation counts" or "no fire on first run"; be consistent).
3. **Wire diff + alerts + push into the v2 run path**, mirroring `cli.py` 555–685. The v2 run is `run_v2` / `run_v2_pipeline` (routed from `cli.py` when `schema_version==2`). After the v2 CSV is written, evaluate `profile.alerts` against the v2 survivors + `load_previous_run(slug, exclude=<the v2 csv just written>)`, persist alerts state, and call `notify_material_change`. Keep the no-fabrication rule: only real survivor hosts/prices feed the alert.
4. **Tests (write WITH the code).** Use committed fixtures only (`worker/tests/fixtures/...`, `PRODUCT_SEARCH_PRODUCTS_DIR`/`PRODUCT_SEARCH_REPORTS_DIR` overrides — ADR-062). Cover: new host appears → fires; same hosts → no fire; first run → per your chosen policy; price_below still fires on the v2 path. Add a v2 profile fixture with a `new_vendor_carries` alert.
5. **Confirm `profile_v2.peek/load` round-trips** the new alert shape (the web save route re-attaches alerts on edit; the loader must accept them).

---

## TASK 2 — Verify schedule editor + iPhone push end-to-end on the v2 pipeline

This is verification, not a rewrite. Confirm the Phase-17 flows work for a `schema_version:2` profile:
1. **Schedule:** `ProfileV2.schedule` reuses v1 `Schedule` (confirmed in profile_v2.py). Check the web schedule editor (`web/app/.../schedule` or the editor component — grep for the Phase-17 editor) writes a `schedule:` block a v2 profile accepts, and that the scheduled GitHub Action / `cli scheduler-tick` would pick up a v2 profile. Read, don't rewrite.
2. **Alerts editor:** confirm the web alerts editor can produce a `new_vendor_carries` rule and a `price_below` rule in the v2 profile's `alerts:` shape your Task 1 types accept. If the editor only knows v1 shapes, add the minimal `new_vendor_carries` option (coordinate the YAML shape with Task 1 step 1).
3. **Push:** confirm `notify_material_change` (ADR-057) is reached on the v2 path (you wired it in Task 1). A real device push needs the VAPID env (present locally) + a subscription; if you can't trigger a real push, assert the call is made (test/log) and note it.
4. If you touch any web UI: **test it at 375px** before calling it done (non-negotiable; see Task 0 for the local-browser technique).

---

## Hard rules (CLAUDE.md — do not violate)
- **No fabrication (ADR-001):** the LLM never produces a price/stock/URL/host. Alerts fire only on real survivor data. The diff + evaluator are pure deterministic Python.
- **Fixtures, not live sites** for tests (`worker/tests/fixtures/`). Tests/CI must never depend on a live `products/<slug>/`.
- **Vendor quirks** go in the registry, not a profile — but Phase 35 is unlikely to touch vendor quirks at all.
- **Secrets** (`.env`, `web/.env.local`) are gitignored; never commit them. They already hold the keys you need.
- Stay in scope. Unrelated issues → "noticed but deferred" in PROGRESS.md, don't fix.

## Verification checklist before commit (all must pass)
- Worker: `python -m pytest -q` green (was 495; +N for your new tests), `ruff check src tests` clean, `mypy` clean.
- **If you changed `worker/`:** run the CI-parity check (memory `reference_uv_match_ci_python`): `cd worker && uv venv --python 3.12 && uv pip install -e ".[dev]" && uv run pytest -q` (or the documented form) — CI runs Python 3.12; local may be 3.13.
- If you touched `web/`: `npm run test:guards` + `npm run test:parity` + `npx tsc --noEmit` + `npx eslint` + `npm run build` all green; mobile 375px checked.

## End of session (in this order)
1. Update `docs/PROGRESS.md`: mark Phase 35 done, set NEXT = Phase 36 (retire `universal_ai` + `vendor_quirks` bot-wall machinery + dead v1 code/tests — REBUILD_PLAN §10), record the Task-0 smoke result, prune stale "noticed/deferred". Keep it ≤ ~150 lines (archive superseded blocks to `PROGRESS_ARCHIVE.md`).
2. Append **ADR-138** to `docs/DECISIONS.md` (new bullet at the top of the Index) describing what you implemented.
3. Commit (format: `phase 35: <summary>` + bullets + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`), then `git fetch origin` and push `origin/main` (pre-authorized).
