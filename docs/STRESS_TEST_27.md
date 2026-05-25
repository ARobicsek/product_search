# Phase 27 — Fix the 3 Phase 26 defects + live re-verify (verification)

**Date:** 2026-05-25
**Brief:** [PHASES.md § Phase 27](PHASES.md)
**Predecessor:** [STRESS_TEST_26.md](STRESS_TEST_26.md) (the defect list this phase closes)
**Fix commit:** `0974299` — "phase 27: ship D1+D2+D3 fixes for Phase 26 defects"
**Throwaway slug onboarded (deleted at end):** `stress27-mx3s`
**Tools:** deployed prod web app (`ari-product-search.vercel.app`), Chrome DevTools MCP (375×812 mobile render check), `cli probe-url --render --detail` (microcenter re-probe)
**Spend this session:** onboarder ~$0.09 + one Run-now ~$0.077 + 3 microcenter probes (trivial) ≈ **$0.17** — well under the $2–5 budget.

> **Note on environment:** AlterLab was heavily degraded for the whole session
> (browser-pool exhaustion, 504s on detail probes, 2.3 KB Amazon stubs). This
> turned out to be *useful* — it's exactly the degraded condition the ADR-084
> callout and the D1 "don't silently drop B&H" behaviour are meant to handle —
> but it also made fresh onboards slow (a B&H detail probe hung the first
> live mx3s onboard for ~14 min). See the D2/D3 live-scope note below.

---

## TL;DR

- **D1 — PASS (live, both paths).** With the new prompt deployed, the onboarder
  KEPT the B&H detail URL in `sources` with `page_type: detail` +
  `extra.probe_note` (despite `detailExtractable:false` + 0 search anchors) —
  exactly the ADR-079 behaviour. The new deterministic save-guard correctly
  stayed *silent* on this healthy shape (no false positive). On Run-now, B&H
  surfaced in the Sources table (`bhphotovideo.com | ok | 0 | 0`) and got a
  classified `transient` bullet in the ADR-084 callout — it did NOT silently
  disappear. Mobile render at 375 px clean.
- **D2 — PASS (unit test = primary proof; live run = no-regression confirm).**
  `test_build_zero_reason_callout_includes_per_source_httperror` +
  `test_passed_match_key_carries_source_url_for_same_host_disambiguation`
  prove per-source accounting. The mx3s Run-now produced 5 sources with
  distinct, correct per-source `passed` counts (ebay 19, amazon 0, target 3,
  bestbuy 3, bhphoto 0) and every 0-result source got the right callout
  category — no host-aggregation collapse.
- **D3 — PASS (decision: KEEP the block).** Re-probed 3 distinct microcenter
  detail URLs (CPU/SSD/motherboard) at the registry defaults → **0 of 3
  succeeded**. The Phase 26 stress26-mc success was a Cloudflare cache-hit
  outlier. Per the brief's 0-or-1 rule the `known_failure` stays `blocker`;
  the registry summary now carries a 2026-05-25 re-verification note.

---

## Per-defect verification

### D1 — make ADR-079 hard to bypass *(P1)* — **PASS**

**Fix shipped (commit `0974299`):**
1. Prompt rule (`onboard_v1.txt` → `promptText.ts` via `sync-prompt.js`):
   a detail-preferred host whose probe fails MUST stay in `sources` with
   `extra.probe_note`; NEVER drop to `sources_pending`; NEVER emit a URL-less
   placeholder.
2. Deterministic save-time guard `web/lib/onboard/detail-preference-presence.ts`
   wired into `/api/onboard/save` — flags any URL-less `universal_ai_search`
   entry in `sources_pending` (names the host from the note text when possible).
3. 5 new cases in `web/scripts/check-onboard-guards.test.mjs` (11/11 green).

**Expected:** the saved profile has a B&H source in `sources` (with
`probe_note` if the probe failed) OR the new deterministic warning fires if
the LLM still drops it. NOT an empty-URL `sources_pending` entry. On Run-now,
B&H contributes listings OR appears in the ADR-084 callout with a classified
reason.

**Actual (both the regression AND the fix observed live):**
- **First onboard attempt** (immediately after the push, before the Vercel
  deploy carrying the new prompt was live): the onboarder hit
  `detailExtractable:false` + 0 search anchors on B&H and produced the **exact
  Phase 26 bypass shape** — a URL-less `sources_pending` entry with the URL
  buried in the `note:` text. This is the regression D1 targets; the new
  deterministic guard + the unit test
  `URL-less placeholder for B&H Photo triggers warning + names the host`
  catch precisely this shape.
- **Second onboard** (new prompt deployed): the onboarder KEPT the B&H detail
  URL in `sources`:
  ```yaml
  - id: universal_ai_search
    url: https://www.bhphotovideo.com/c/product/1718918-REG/logitech_910_006556_mx_master_3s_wireless.html
    page_type: detail
    extra:
      alterlab_options: { country: us, min_tier: 3, wait_condition: networkidle }
      probe_note: "weak probe 2026-05-25: detailExtractable=false; search URL returned 0 anchors; detail URL retained per ADR-079 (runtime escalation owns retry)"
  ```
  and said so in chat ("B&H Photo detail URL retained despite weak probe
  result … per ADR-079 guidance").
- **Save**: succeeded; the only warnings shown were the pre-existing ADR-067
  coverage advisories (target/bestbuy search-only, bhphoto detail-only). The
  new D1 guard correctly produced **no** warning — confirming it does not
  false-positive on the healthy in-`sources` shape.
- **Run-now**: `bhphotovideo.com | ok | 0 | 0` in the Sources table + a
  `transient` bullet in the `[!NOTE]` callout ("AlterLab couldn't render this
  vendor's page this time…"). B&H did not silently disappear. ✅
- **Mobile 375 px**: Sources table + callout render cleanly; only the wide
  Ranked-listings table scrolls horizontally (intentional, same as Phase 26).

**Verdict: PASS.** Both the prompt path (primary) and the guard's silence on
the healthy shape were observed live; the guard's positive behaviour on the
bypass shape is proven by unit test and matched the regression seen in the
first onboard attempt.

### D2 — fix ADR-084 per-source `passed` accounting *(P1)* — **PASS**

**Fix shipped (commit `0974299`):** the cli now stamps the exact `source_url`
into each emitted `Listing.attrs` at fetch-emit time, and `_passed_match_key`
returns `(source, host, url)` so multiple URLs on the same host get distinct
`passed` counts instead of collapsing to the per-host total. Sources-table and
ADR-084 callout both read the corrected per-source count.

**Expected:** an `error: HTTPError …` row on a host whose sibling URL succeeded
now shows `passed = 0` and lands in the callout as a `transient` bullet.

**Actual:**
- **Unit test (primary proof, per the brief):**
  `test_build_zero_reason_callout_includes_per_source_httperror` mirrors the
  live stress26-xm5 shape (one `bestbuy.com` row `ok 4/2` + three
  `bestbuy.com` rows `error: HTTPError … 0/0`) and asserts all three error
  rows render as `transient` bullets. `test_passed_match_key_carries_source_url_*`
  proves same-host URLs get distinct keys. Green (17/17 in `test_cli.py`;
  336/336 worker suite).
- **Live no-regression confirm (mx3s Run-now):** five sources with distinct,
  correct per-source `passed` counts (ebay 19, amazon 0, target 3, bestbuy 3,
  bhphoto 0); each 0-result source classified correctly (amazon `no results`,
  bhphoto `transient`). No host-aggregation collapse.

**Live-scope note:** the *exact* "same host, one URL ok + N URLs HTTPError"
shape requires a multi-detail-URL vendor (the xm5 multi-variant case). The
brief states explicitly that the unit test is the primary proof and the live
reproduction is only a confidence check ("D2 PASS doesn't require this"). With
AlterLab degraded enough to hang fresh onboards this session, I did not spend
budget forcing a second multi-variant onboard; the unit test + the mx3s
no-regression run are the proof of record.

**Verdict: PASS** (unit test primary; live run shows correct per-source
accounting with no regression).

### D3 — re-probe microcenter and update the registry *(P2)* — **PASS (KEEP)**

**Re-probe (2026-05-25, `cli probe-url --render --detail` at registry
defaults `country: us, min_tier: 3, wait_condition: networkidle`):**

| # | Category | Origin | Body | Outcome |
|---|---|---|---|---|
| 1 | CPU (Ryzen 7 9700X) | 200 | 39 chars | FAIL — empty stub |
| 2 | SSD (Samsung 990 Pro 2TB) | 200 | 32,114 chars | FAIL — Cloudflare challenge, 0 listings |
| 3 | Motherboard (ASUS X870E-E) | 200 | 32,030 chars | FAIL — Cloudflare challenge, 0 listings |

Full evidence: [docs/microcenter_reprobe_2026_05_25.md](microcenter_reprobe_2026_05_25.md).

**Decision rule (from the brief):** 0-or-1 of 3 succeed → KEEP the block. **0
of 3 succeeded**, so the `known_failure` stays `severity: blocker`. The Phase
26 stress26-mc success (`1/1 @ $279.99`) was a single Cloudflare cache-hit
outlier, not a recovery. `vendor_quirks.yaml` updated with a 2026-05-25
re-verification note; `promptText.ts` + `vendor-quirks-data.ts` regenerated
via `sync-prompt.js`.

**Consequence for the onboarder:** microcenter URLs correctly continue to
route to `sources_pending` (the registry behaviour is unchanged, now backed by
fresh evidence). No live stress27-mc onboard was run — D3 is settled by the
direct probe evidence, and a live onboard would only reconfirm the unchanged
`sources_pending` routing.

**Verdict: PASS** (the registry now reflects verified-current reality; the
underlying Cloudflare bypass remains UNSOLVED — same as the standing
"Noticed but deferred / microcenter.com Cloudflare bypass" line item).

---

## Done-when checklist (from the brief)

- [x] All three defect fixes landed (prompt regen + new TS check + worker test
  + registry update with web artifact regen) — commit `0974299`.
- [x] Worker suite green (`pytest` 336/336; `ruff` + `mypy` clean on `cli.py`).
- [x] Web `tsc` 0 errors; `eslint` pre-existing warnings only (4, unrelated
  files); `test:parity` 2/2; `test:guards` 11/11 (incl. the new D1 cases);
  `next build` compiled.
- [x] Per-defect live re-verification (this doc): D1 PASS (live), D2 PASS
  (unit-primary + live no-regression), D3 PASS (KEEP, probe-evidence).
- [x] ADR-079 / ADR-084 / ADR-068 amended (status-update entries appended, not
  rewritten) — see DECISIONS.md.
- [x] All `stress27-*` slugs deleted via the Phase 16 path; live products
  confirmed untouched on `origin/main` — done (cleanup section below).

## Cleanup verification (end-of-phase)

Only `stress27-mx3s` was ever saved (the xm5/mc live onboards were not run —
see the D2/D3 verdicts above). Deleted via the home-page delete button (Phase
16 hard-delete), commit `b2664f0` ("chore: delete product stress27-mx3s").
Post-delete `git fetch origin` confirms:
- `products/stress27-mx3s/` absent from `origin/main` ✅
- `reports/stress27-mx3s/` absent from `origin/main` ✅
- Live products untouched (amd-epyc-9255, aufschnitt-essiccata-jerky,
  breville-barista-express, dyson-v15-detect-vacuum,
  lululemon-never-lost-keychain-wordmark, nvidia-rtx-5090, sony-wh-1000xm5,
  the-netanyahus-joshua-cohen, test-product all present and unchanged) ✅
