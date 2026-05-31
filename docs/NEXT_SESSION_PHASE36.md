# Next session — Phase 36 (Retire legacy scraping infrastructure)

**You are a fresh session. Follow this brief literally and in order. Do not improvise scope.**
Read these first, in this order, and nothing else until told:
1. `docs/PROGRESS.md` — "Most recent work" + "Current state" + "Blockers".
2. This file (top to bottom).
3. `docs/REBUILD_PLAN.md` §10 (Phase 36 row).

**Branch:** `main`. **One phase per session — do Phase 36 only.** Push is pre-authorized (CLAUDE.md); never `--no-verify`.

---

## 0. Pre-flight (run these, confirm green, then stop and read)
```bash
cd /c/dev/product-search
git fetch origin && git status            # expect clean / in sync; if behind, git pull --rebase origin main
```
Baseline you must NOT regress:
- Worker: `cd /c/dev/product-search/worker && python -m pytest -q`
- Web: `cd /c/dev/product-search/web && npm run test:guards` + `npm run test:parity`

---

## TASK 1 — Delete Legacy Universal AI Adapter and Vendor Quirks

Since the rebuild to the `schema_version: 2` (Serper + eBay) architecture is complete and successfully tested, we no longer need the complex, fragile scraping infrastructure.

1. **Delete `universal_ai.py`**: Remove the legacy adapter that handled AlterLab, Scrappey, curl_cffi, and httpx cascades from `worker/src/product_search/adapters`.
2. **Remove Vendor Quirks Parsing**: In `registry.py` (or wherever `vendor_quirks.yaml` is loaded), remove the bot-wall flags (`use_scrappey`, `skip_alterlab`, `alterlab_known_good`, `force_detail_backup`, `search_url_template`). You may be able to delete the entire `vendor_quirks.yaml` system if it's no longer used at all in v2.

---

## TASK 2 — Delete Legacy v1 Onboarder Probe Routes

1. **Remove `/api/onboard/probe`**: Delete the entire folder and route (`web/app/api/onboard/probe/route.ts`).
2. **Delete probe utilities**: Remove legacy v1 onboarder's per-vendor probing apparatus (`probe-url.ts`, `probe-and-update.ts`) from `web/lib/onboard/`.

---

## TASK 3 — Delete Dead Tests

1. **Worker Tests**: Delete `test_universal_ai.py` and any tests specifically targeting `vendor_quirks.yaml` or bot-wall behavior.
2. **Web Tests**: Remove tests in `web/scripts/` or `web/lib/` that assert behavior for the retired v1 probe logic. Ensure the test suites pass cleanly after deletion.

---

## TASK 4 — Documentation Cleanup

1. **Update Docs**: Update `PROGRESS.md`, `README.md`, and any other architectural docs to reflect the permanent removal of the legacy scraping tier.

---

## Verification checklist before commit (all must pass)
- Worker: `python -m pytest -q` green, `ruff check src tests` clean, `mypy` clean.
- Web: `npm run test:guards` + `npm run test:parity` + `npx tsc --noEmit` + `npx eslint` + `npm run build` all green.

## End of session (in this order)
1. Update `docs/PROGRESS.md`: mark Phase 36 done, note that the legacy infrastructure has been purged. Keep it ≤ ~150 lines.
2. Commit (format: `phase 36: <summary>` + bullets), then `git fetch origin` and push `origin/main` (pre-authorized).
