# Session B brief — Phase 29 recall-investigation handoff

> **Audience**: a fresh coding-agent session, possibly running a less capable model than Opus. Be literal. Don't improvise architecture; this brief tells you exactly which files to touch, what shape the data takes, and what "done" looks like for each sub-task. If something looks wrong, stop and ask the user — do NOT invent fixes.

## Before you do anything

1. Read these in order, no exceptions:
   - `docs/PROGRESS.md` (state)
   - `docs/SESSION_PROTOCOL.md` (rules)
   - This file
   - `docs/DECISIONS.md` sections for **ADR-067, ADR-103, ADR-104, ADR-105, ADR-106, ADR-107, ADR-109, ADR-111** (most recent at the top — skim, don't memorize). Don't re-debate decided points.
2. Run `git fetch origin && git pull --rebase --autostash origin main`. The web app commits to `origin/main` between sessions; never trust local `products/*/profile.yaml` or `reports/**`.
3. Confirm the worker test suite is green: `cd worker && python -m pytest -q` (expect 412+ passed). If not green, STOP and ask the user.

## Why this session exists — read the receipts

Production run [`reports/dji-neo-2-motion-fly-more-combo/2026-05-28.json`](../reports/dji-neo-2-motion-fly-more-combo/2026-05-28.json) (committed to `origin/main` 2026-05-28 03:04:47 UTC) returned 7 universal_ai sources, of which **4 underperformed**:

| Host | Body | Status | Root-cause suspicion |
|---|---|---|---|
| amazon.com | 2,317 bytes | `transient` (bot wall) | ADR-107 Scrappey fallback should have fired — either didn't, or it fired and Scrappey also got walled. **Defect 1.** |
| bhphotovideo.com | 6,203 bytes | `transient` | Scrappey is Tier 1 (`use_scrappey: true` in `vendor_quirks.yaml`) — Scrappey itself returned a 6KB Cloudflare challenge page. **Defect 2.** |
| backmarket.com | 6,458 bytes | `transient` | Same as B&H — Scrappey returned a CF challenge. **Defect 2.** |
| centralcomputer.com | 374,745 bytes | `parser_gap` | Scrappey worked, but the universal extractor found 0 listings on a fully-rendered 375KB page. **Defect 3.** |

Two other sources returned a clean 0-match that the diagnostic mislabeled:

| Host | Reality | Reported | Defect |
|---|---|---|---|
| microcenter.com | URL was correct (`Ntt=…` keyword search); Microcenter genuinely doesn't carry the DJI Neo 2 | "search URL may be mis-scoped" | **Defect 4** — mis-scope diagnostic is too eager |
| target.com | Same shape: 18 listings, all real Target drone listings, none matched the requested SKU | "search URL may be mis-scoped" | **Defect 4** |

Walmart was missing from the run entirely because the onboarder demoted it to `sources_pending` during a 504 probe; the user moved it back via edit afterward. That is **expected behavior** post-ADR-110; not in scope for this session.

## What was already done before this session (do NOT redo)

- **Session A (2026-05-28)** — UI/UX fixes:
  - Save button now resets on the next chat turn (`OnboardChat.tsx`).
  - `force_detail_backup` is now a hard save-time error (ADR-111), so the onboarder cannot save amazon/target/walmart with only a search URL.
  - Prompt regenerated; `web/lib/onboard/promptText.ts` + `vendor-quirks-data.ts` are current.

- **Recall plumbing already in place** (read before adding more):
  - ADR-103: thin-body classifier (`THIN_BODY_CEILING=15000` in `source_reasons.py`, `_WEAK_BODY_FLOOR=2000` in `universal_ai.py:767`).
  - ADR-104: Scrappey is wired as Tier 1 for vendors with `use_scrappey: true` (`universal_ai.py:_fetch_html` lines 240-326).
  - ADR-106: `__NEXT_DATA__` embedded-state extraction (`universal_ai.py:_extract_via_embedded_state`).
  - ADR-107: post-extract Scrappey fallback for `alterlab_known_good` thin-body bot walls (`universal_ai.py:3121-3171`).
  - ADR-109: per-source rejection attribution by `match_url` (so `dominant_rejection == "relevance_check"` triggers the mis-scope message per host).

## The four sub-tasks

Pick them up **in this order**. Each is independently shippable. Do not try to do all four in one PR — commit after each.

---

### B-1. Add Scrappey diagnostics logging (do FIRST — unblocks B-2)

**Why**: today, when Scrappey returns a 6KB CF challenge instead of a rendered page, the diagnostic just says "transient — body 6,203 chars." We can't tell if Scrappey ran, what it returned, what proxy exit IP it used, or whether the body was a CF challenge vs a legitimate small page. Without these logs, B-2 is guesswork.

**Files to touch**:
- `worker/src/product_search/adapters/universal_ai.py` — `_fetch_via_scrappey` (line ~388) already logs exit IP via `logger.info`. That's good but isn't surfaced in the run report JSON. Add a structured per-attempt record to `tls.scrappey_diagnostics`.
- `worker/src/product_search/cli.py` — wherever the `source_stats` row is built for a `universal_ai_search` source, include the per-host scrappey diagnostics under a new `scrappey_attempts` field. (Use `Grep` for `source_stats` + `display_source`; the assembly happens in `_cmd_search`.)
- `web/lib/report-types.ts` (or wherever the source-stats type lives — search for `fetched: number`) — extend the type so `scrappey_attempts` is rendered.
- `web/app/[product]/SourcesPanel.tsx` (or wherever per-source diagnostics show) — add a small "Scrappey: 1 attempt, 6,203 chars, exit IP US, looks like CF challenge" line under the source's reason text. Keep it muted/secondary; not every source uses Scrappey.

**Data to record per Scrappey attempt**:
```ts
{
  url: string,           // first 80 chars; PII-safe
  body_len: number,
  origin_status: number, // Scrappey's `solution.statusCode`
  exit_ip: string | null,
  exit_country: string | null,
  exit_hosting: boolean | null,
  cf_challenge: boolean, // body matches _WEAK_RENDER_SIGNATURES regex (universal_ai.py:756)
  triggered_by: 'tier1_configured' | 'dynamic_weak_render_fallback' | 'adr107_post_extract',
  elapsed_ms: number,
}
```

**Done when**:
- A live run that hits any Scrappey path emits the new `scrappey_attempts` array in `reports/<slug>/<date>.json` under each source's record.
- The web UI shows the per-attempt summary line under the source's diagnostic message.
- Unit tests (`worker/tests/test_universal_ai.py`): one test asserting `tls.scrappey_diagnostics` is populated when `_fetch_via_scrappey` is called (mock the httpx client). Worker green.
- `node web/scripts/sync-prompt.cjs` re-run is NOT required (no prompt or registry change). Just `tsc --noEmit` + `eslint` + `next build`.

**Don't**:
- Don't add Scrappey diagnostics to `LAST_RUN_LOG` (that's the ai_filter rejection log, different concern).
- Don't change Scrappey's URL or payload yet; B-2 owns that.

---

### B-2. Diagnose & fix Scrappey not bypassing B&H / Backmarket

**Why**: both vendors have `use_scrappey: true` and `proxy_country: UnitedStates` in `vendor_quirks.yaml`, yet returned 6KB bodies on the 2026-05-28 run. The `KNOWN QUIRK (warning)` summary in `vendor_quirks.yaml` says these are "Bypassed via Scrappey" — that claim isn't holding live.

**Hypothesis order (test each)**:
1. **The Scrappey free/PAYG tier isn't using JS-rendered mode by default.** Today the payload (`universal_ai.py:404-409`) is:
   ```python
   payload = {"cmd": "request.get", "url": url, "proxyCountry": proxy_country}
   ```
   Scrappey's `request.get` is the basic HTTP fetch mode. For CF-walled pages, the docs recommend adding `"session": <id>` (reuses cookies across calls), `"keepHeaders": true`, or switching to `"cmd": "request.get"` with a `browserActions` array that waits for `networkidle`. Check Scrappey's current docs (use `WebFetch` on `https://scrappey.com/docs`) and confirm the exact param for "render-with-JS + wait for CF challenge to clear."
2. **The proxy country may be wrong.** B&H may IP-block US residential proxies in certain blocks. Try `proxy_country: ""` (Scrappey's default rotation) on a captured probe — does the 6KB go away?
3. **Cookies/session.** A bare `request.get` doesn't persist the CF clearance cookie. If hypothesis 1 fixes it via `browserActions`, this becomes moot.

**Process** (don't skip steps — the cost is bounded and the diagnosis is more valuable than the fix):
1. Capture today's failing body once via the B-1 logging — DO NOT re-spend Scrappey credits to re-confirm. The user's run already captured both 6KB bodies; if they're not in the report, ask the user to share the captured `s_html` from a one-off `cli search` against the DJI-Neo-2 profile with `LOG_LEVEL=DEBUG`. Save under `worker/tests/fixtures/scrappey/`:
   - `bh_dji_cf_challenge.html` (the 6KB body we DO have)
   - `backmarket_dji_cf_challenge.html` (likewise)
2. Verify these ARE CF challenges (not legitimate "no results" pages): grep for `_WEAK_RENDER_SIGNATURES` patterns (`just a moment`, `checking your browser`, `cdn-cgi/challenge`, etc.) in the captured HTML. Document the verdict in a one-line comment in the fixture file.
3. **Read Scrappey's current docs** via `WebFetch https://scrappey.com/docs`. Identify the exact payload shape for "browser-rendered fetch with CF bypass + wait-for-load." Update `_fetch_via_scrappey` payload accordingly.
4. Run a live re-probe against B&H + Backmarket with the new payload. **Budget: 5 Scrappey requests total (€0.005, well under the user's tolerance).** If the body comes back ≥50KB and contains real product anchors, ship the fix. If it still returns ≤15KB, document the finding in the ADR and demote both hosts in `vendor_quirks.yaml` from `severity: warning` back to `severity: blocker` with a note that Scrappey free-tier is insufficient.
5. Add a regression test using the captured fixture: pre-fix the body is a CF challenge classified as `transient`; post-fix (the new payload would have returned a real body) the extractor finds ≥1 listing.

**Files to touch**:
- `worker/src/product_search/adapters/universal_ai.py` (`_fetch_via_scrappey` only; do not refactor)
- `worker/src/product_search/vendor_quirks.yaml` (one of: update the `KNOWN QUIRK` notes, or demote severity)
- `worker/tests/fixtures/scrappey/*.html` (new directory)
- `worker/tests/test_universal_ai.py` (1-2 new tests)
- `docs/DECISIONS.md` (new ADR-112: "Scrappey payload tuned for CF bypass" OR "Scrappey free-tier insufficient; demote B&H/Backmarket")
- `web/scripts/sync-prompt.cjs` is NOT required (vendor_quirks notes/severity change is read at runtime; only `prefer_page_type` / `search_url_template` / `force_detail_backup` flow into the prompt).

**Done when**:
- A live re-probe of B&H + Backmarket either (a) returns real product anchors → ship, or (b) is documented as Scrappey-insufficient → hosts demoted, onboarder told to route to `sources_pending`. **Either outcome is a valid completion.** Don't keep iterating if Scrappey can't do it; document and move on.
- Regression test covers the captured-body classification.
- Worker green; web `tsc`/`eslint`/`test:guards`/`test:parity`/`next build` clean.

**Don't**:
- Don't pay for more than 5 live Scrappey requests in diagnosis. If it isn't fixed by 5 requests, demote.
- Don't change ADR-107 logic — only the Scrappey fetch payload.
- Don't generalize "JS-render mode" to all vendors; B&H and Backmarket are the proven CF-walled cases, and other vendors may not need the extra wait time.

---

### B-3. Verify ADR-107 fires for Amazon thin-body

**Why**: Amazon on 2026-05-28 returned a 2,317-byte body (CF/bot wall) and the report still shows that body. Three possibilities:
1. ADR-107 didn't fire — possible bug.
2. ADR-107 fired, Scrappey also returned a thin body (CF challenge), `s_merged` was empty so the diagnostics weren't updated — current behavior is silently to keep the original body.
3. ADR-107 fired and recovered, but something upstream zeroed listings.

You won't know which until B-1 ships the diagnostics. AFTER B-1 is committed, you can read the report and see.

**Files to touch** (in this order):
1. Read [`universal_ai.py:3121-3171`](../worker/src/product_search/adapters/universal_ai.py) — the ADR-107 path.
2. If the diagnostics show ADR-107 fired but Scrappey returned a thin body, **also surface that attempt** (B-1 covers this — verify the `triggered_by: 'adr107_post_extract'` field is populated).
3. If the diagnostics show ADR-107 didn't fire, trace why: print the values of `not merged`, `scrappey_key`, `len(html) < THIN_BODY_CEILING`, `quirks.get('alterlab_known_good')`, and `not (alterlab_options and alterlab_options.get('use_scrappey'))` at the conditional. The fix is wherever the condition silently evaluates False.
4. Add a unit test (`worker/tests/test_universal_ai.py`):
   - Mock `_fetch_html` to return a 2,317-byte body.
   - Mock `_fetch_via_scrappey` to return a 50KB body with one product anchor.
   - Assert `fetch(amazon_query)` returns ≥1 listing (i.e., ADR-107 fired and recovered).
   - Assert `tls.last_fetch_diagnostics['final_fetcher'] == 'scrappey'` and `body_len == 50000`.

**Done when**:
- The new test passes pre-fix only if the code was already correct; otherwise it fails first, then passes after your fix.
- A live re-run of the DJI-Neo-2 profile shows either Amazon returning real listings (good) or a Scrappey attempt logged with `cf_challenge: true` (correctly identified as still walled — defer to whatever B-2 concluded).
- Worker green.

**Don't**:
- Don't bump `_WEAK_BODY_FLOOR` from 2000 — that's the escalation-ladder threshold for `_fetch_with_escalation`; ADR-107's gate is `THIN_BODY_CEILING=15000` which is correctly broad.
- Don't change the no-double-charge guard (`not (alterlab_options and alterlab_options.get('use_scrappey'))`). It's correct: don't Scrappey-retry a vendor that already Scrappey'd.

---

### B-4. Refine `NO_MATCH` mis-scope diagnostic

**Why**: ADR-109 made per-source attribution reliable, so when Microcenter's 24 rejections came back as `dominant_rejection == "relevance_check"`, the UI correctly said "search URL may be mis-scoped." But on the 2026-05-28 run, the URL was CORRECT (`Ntt=` keyword search); Microcenter just doesn't carry the DJI Neo 2. The same is true for Target (18 real Target drone listings, none matched the requested SKU). The current message sends the user to fix a URL that's already fine.

**Hypothesis for the refinement**: when the AI filter's rejection reasons cluster on "different product entirely" / "wrong model" (e.g., "DJI Mic, not DJI Neo 2"), the URL is fine — the vendor just doesn't stock it. When they cluster on "this is a category page tile" / "this is unrelated to the brand" / "brand match but not the model line" (e.g., the original DJI Neo 2 mis-scope where Microcenter served the entire DJI catalog including Mic 3, Osmo, RS 5 gimbals), the URL IS the problem.

**Files to inspect first** (don't change anything until you've read all of them):
- `worker/src/product_search/source_reasons.py` — find where the `NO_MATCH` / "search URL may be mis-scoped" message text lives.
- `worker/src/product_search/cli.py:annotate_dominant_rejections` (added in ADR-109) — this is where `dominant_rejection` is computed per source.
- `worker/src/product_search/validators/ai_filter.py:LAST_RUN_LOG` — see the shape of each rejection entry (look for the `reason` field).
- The captured Microcenter mis-scope fixture: `worker/tests/fixtures/universal_ai/dji_microcenter_misscope_filterlog.jsonl` — what do the rejection reasons look like for the GENUINE mis-scope?
- Look at the 2026-05-28 filter.jsonl for Microcenter (24 rejections) and Target (18 rejections) — what do the rejection reasons look like for the "vendor doesn't carry it" case?

**Likely shape of the fix**:
- In `cli.annotate_dominant_rejections` (or a sibling helper), add a second-tier classification when `dominant_rejection == "relevance_check"`:
  - If ≥70% of rejection reasons mention the SAME product name as `display_name` (i.e., the vendor served the right brand/line, just different models) → likely **wrong URL** (genuine mis-scope) → use the current "search URL may be mis-scoped" message.
  - Otherwise → likely **vendor doesn't carry this product** → use a new message: `"<host> returned <N> listings but none matched the specific product you're tracking. The URL appears correct, so <host> likely doesn't stock this product right now — leave it active so the carry-gate auto-wakes it if stock arrives, or remove it via Edit Profile if you don't want it polled."`
- The heuristic is fuzzy; do NOT over-engineer it. A simple substring check of `display_name` tokens in rejection reasons is fine for v1.

**Files to touch**:
- `worker/src/product_search/source_reasons.py` (new `NO_MATCH_VENDOR_DOESNT_CARRY` reason variant)
- `worker/src/product_search/cli.py` (`annotate_dominant_rejections` or call-site that maps to reason text)
- `worker/tests/test_cli.py` (add 2 fixture-backed tests: one for genuine mis-scope, one for vendor-doesnt-carry — use the existing DJI Microcenter fixture for the first; capture a small Target/Microcenter fixture from the 2026-05-28 run for the second)
- `web/app/[product]/SourcesPanel.tsx` is NOT required if the message field is just text — it already renders whatever string the JSON sidecar gives it.

**Done when**:
- The captured DJI Microcenter mis-scope fixture continues to emit the existing "URL may be mis-scoped" message (regression).
- A new captured fixture (Microcenter/Target on 2026-05-28 with vendor-doesnt-carry rejections) emits the new "vendor likely doesn't stock" message.
- Worker green.

**Don't**:
- Don't try to use an LLM to classify the rejection reasons. The heuristic is "substring of `display_name` tokens"; we don't need ML for this.
- Don't change ADR-098 / ADR-109 attribution code; just add a downstream classification layer.

---

## Out of scope for Session B (don't touch)

- The Walmart pending-during-onboarder defect (root cause already understood: 504 during probe; user manually re-promoted; ADR-110 turn budget reduces but doesn't eliminate the chance).
- ADR-107's logic for `alterlab_known_good`. Leave the gate alone; the question is whether Scrappey itself works (B-2), not whether the gate's predicate is right.
- CentralComputer parser-gap on 374K body. Capture it under `worker/tests/fixtures/universal_ai/centralcomputer_dji_neo2_parser_gap.html` for a future session, but DO NOT try to write a new extractor for it this session — that's a separate ADR-106-style investigation worth its own session.
- Reducing the ADR count / docs hygiene.
- Mobile popover layout.

## Session-close protocol

1. Update `docs/PROGRESS.md`:
   - Move completed B-N items to "Most recent work" with one-line summaries.
   - Note remaining items under "Active phase" so the NEXT session picks them up.
2. Add Consequence text to ADR-112 (and any others you wrote) noting what shipped and what tests cover it.
3. Commit per CLAUDE.md (no `--no-verify`; standard prefix `phase 29: ADR-112 ...`).
4. **Push is pre-authorized** — push to `origin/main` without asking.
5. If you finish any sub-task and have <30 min left, STOP and update PROGRESS.md rather than starting the next one mid-session.
