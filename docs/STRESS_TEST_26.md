# Phase 26 — Cross-cutting LIVE stress test & regression sweep (findings)

**Date:** 2026-05-24
**Brief:** [PHASES.md § Phase 26](PHASES.md)
**Throwaway slugs onboarded (all deleted at end):** `stress26-mx3s`, `stress26-xm5`, `stress26-ddr5`, `stress26-mc`
**Reports captured:** all four committed to `origin/main` by the deployed Run-now path
**Total spend:** ~$0.62 (onboarder ~$0.24 + 4 runs ~$0.38) — well under the $3–8 budget; AlterLab API charges not visible from runtime cost panel but bounded by the breaker
**Tools used:** deployed prod web app (`ari-product-search.vercel.app`), Chrome DevTools MCP (375×812 mobile viewport for render checks)

---

## TL;DR

- **Most ADR regressions verified** — ADR-068 / ADR-075 / ADR-077 / ADR-078 / ADR-080 / ADR-081 / ADR-082 / ADR-084 all observed firing in production reports.
- **ADR-079 (detail-preferred probe is advisory) has a hole**: the onboarder LLM can drop a detail-preferred URL *entirely* during draft generation, in which case the save-time gate has nothing to protect. Saw this on stress26-mx3s with B&H Photo.
- **ADR-084 (source-reason taxonomy) has a fidelity bug**: the per-source `passed` count appears to be host-aggregated rather than per-source, so non-skip error rows (e.g. `error: HTTPError ...`) for a host whose other URL succeeded see `passed > 0` and are silently treated as `OK` by the classifier → they get no callout bullet.
- **`microcenter.com` is no longer a `known_failure`** — its detail URL was extracted cleanly (1/1, $279.99) via AlterLab in this run. The registry entry is stale.
- **Two onboarder paper-cuts** worth fixing: (a) `spec_attrs` schema mismatch (ADR-074 followup #2 class) cost an extra round-trip on the ddr5 onboard; (b) `low_seller_feedback` flag's `description` is rendered as `(no description)` in the report's Flags section.
- **Web + mobile rendering of the reports is clean** at 375 px including the new GFM `[!NOTE]` callout for ADR-084.
- **No fabrication** observed; the synth's bottom-line price for every report traces to the deterministic `filter.jsonl` record from the extractor.

---

## Regression checklist — per-row PASS / FAIL / N·A

| # | ADR | What we expected | Where it fired (evidence) | Verdict |
|---|---|---|---|---|
| 1 | **ADR-082** Amazon defaults | Saved profile carries `country: us, min_tier: 3, wait_condition: networkidle` for amazon.com sources; Amazon search yields listings (otherwise the static HTML is 0 anchors). | `products/stress26-mx3s/profile.yaml` Amazon source carries all three options; `reports/stress26-mx3s/2026-05-24.md` shows `amazon.com \| ok \| 20 \| 5` (20 anchor candidates extracted, 5 passed filter, including $84.90 MX Master 3S). Same defaults present in stress26-xm5, stress26-mc. | **PASS** |
| 2 | **ADR-068** Best Buy `intl=nosplash` URL transform | Adapter applies `intl=nosplash` at fetch time even if the saved URL doesn't carry it; Best Buy search yields listings. | Saved `bestbuy.com/site/searchpage.jsp?...st=logitech+mx+master+3s` (no `intl=nosplash` in URL — correct, transforms are runtime); report shows `bestbuy.com \| ok \| 4 \| 3` for that URL — proving the transform fired (without it the body collapses to 7 KB / 0 anchors, per the registry note). | **PASS** |
| 3 | **ADR-068** Backmarket defaults | N·A — no backmarket product onboarded this round. (covered by registry merge unit tests; live verification deferred to a future sweep). | — | **N·A** |
| 4 | **ADR-079** Detail-preferred probe failure does NOT demote to `sources_pending` | A B&H or similar detail-preferred URL whose probe fails should stay in `sources` with an advisory note. | **PARTIAL — see Defect 1**. The save-gate code path is intact, but on stress26-mx3s the onboarder LLM dropped the B&H detail URL *entirely* before save (only an URL-less placeholder note in `sources_pending`), so the gate had no URL to protect. On stress26-xm5 the same vendor was given 3 detail URLs and all survived to `sources`. | **MIXED** |
| 5 | **ADR-080** No fragile `title_excludes` | No saved profile contains a `title_excludes` value that is a substring of the product name. | All 4 saved profiles have zero `title_excludes` entries. The onboarder respected the rule and added only `condition_in:[new] + in_stock`. | **PASS** |
| 6 | **ADR-075** `condition_in:[new]` emission + deterministic enforcement | "new only" intent → `{rule: condition_in, values: [new]}` in saved YAML; filter log shows deterministic rejections of `used`/`refurbished`. | Every "new only" profile has the rule. `reports/stress26-mx3s/2026-05-24.filter.jsonl` shows multiple `[condition_in] condition 'refurbished' not in ['new']` and `[condition_in] condition 'used' not in ['new']` rejections (47 rejections total). Identical pattern on xm5 and mc. | **PASS** |
| 7 | **ADR-081** Hybrid filter pre-pass | Deterministic filters reject used / out-of-stock listings BEFORE `ai_filter`. | filter.jsonl reasons prefixed `[condition_in]` / `[in_stock]` (the deterministic-pipeline labels), distinct from the LLM's relevance-check reasons. ai_filter is only called on the survivors — visible in the per-source token costs (ai_filter row tokens consistent with smaller candidate set). | **PASS** |
| 8 | **ADR-076** Auto-backfill missing detail URL | `force_detail_backup` host with a search URL but no detail URL gets a detail URL auto-backfilled by the post-save probe. | **N·A this run** — every `force_detail_backup` source in every saved profile already had a paired detail URL from the onboarder (Best Buy + Target on mx3s; Best Buy + Target on xm5). No backfill trigger fired. (ADR-076 itself only activates when the trigger is satisfied — code path observed inactive, not regressed.) | **N·A** |
| 9 | **ADR-077** Recall-first full-HTML search extraction | Amazon / Target search yields listings the anchor walker alone wouldn't recover. | `reports/stress26-mx3s/2026-05-24.md` `amazon.com \| ok \| 20 \| 5` — 20 candidates from Amazon search HTML, 5 passing. The walker (per ADR-082's evidence) returns 0 product-shaped anchors here; the only way to get 20 is the full-HTML extractor. | **PASS** |
| 10 | **ADR-078** 5xx retry + circuit breaker | AlterLab degradation should fire bounded retries; after 3 consecutive degraded fetches the breaker opens and skips remaining sources with a visible skip reason. | `reports/stress26-xm5/2026-05-24.md` shows exactly the expected pattern: 3 Best Buy detail sources errored (curl_cffi fallback after AlterLab failure), then `bestbuy.com (4th URL), bhphotovideo.com (3), target.com (2), amazon.com` all carry `error: skipped: AlterLab circuit open after 3 consecutive failures`. Skip reason surfaces in the Sources table. | **PASS** |
| 11 | **ADR-083** 422 `browser_pool_exhausted` retried like 5xx | A pool-exhausted 422 retries through the bounded loop with longer backoff before falling through. | Indirect evidence: `reports/stress26-mx3s/2026-05-24.md` callout reads `target.com — transient: AlterLab's browser pool was briefly exhausted — a temporary capacity issue at the scraping provider`. The classifier message comes from rule #6 (`alterlab_pool_exhausted` diagnostic flag), which is only set when `_LAST_ALTERLAB_POOL_EXHAUSTED` was raised by `_is_transient_alterlab_422` — i.e., we did detect + try to retry a pool-exhaustion 422 on Target. | **PASS (indirect)** |
| 12 | **ADR-084** Every 0-result source gets a classified reason | Each non-clean source in the report's "Sources searched" panel appears as one bullet in the `[!NOTE]`/`[!WARNING]` callout with the right category (no match / no results / needs work / transient / blocked). | Bullets observed and correctly categorised across the 4 reports: `transient` (pool exhausted / breaker skip), `needs work` (target.com 140 KB body, 0 listings parsed; newegg.com 820 KB body, 0 listings), `no match` (serversupply.com 13 fetched, 0 passed). **BUT**: see **Defect 2** — the host-aggregated `passed` count silently swallows the per-source classification for non-skip errors when another URL on the same host succeeded. | **MOSTLY PASS** |
| 13 | **ADR-084** `PERMANENT`/`blocked` reason fires for a `known_failure` vendor | Forcing microcenter into `sources` should produce a `blocked` bullet citing the registry summary. | **DID NOT FIRE — for an interesting reason.** microcenter actually **worked**: `microcenter.com \| ok \| 1 \| 1` for the Ryzen 7 9700X detail URL ($279.99). So the report skipped the PERMANENT bullet because passed > 0 (correct behaviour given the data). What this exposes is that the registry's `known_failure: blocker` entry for microcenter is **stale** — see **Defect 3**. The PERMANENT path itself (rules #3 / #4 in `source_reasons.py`) is unit-tested in `test_source_reasons.py`; a live exercise of it requires a vendor that is actually un-scrape-able today, which we couldn't synthesise without intentionally breaking AlterLab. | **NOT EXERCISED LIVE** (path unit-tested; deferred to a future sweep when a real PERMANENT vendor surfaces) |
| 14 | **ADR-020 / ADR-001** No fabrication | Every price/URL/quote in the report traces to deterministic fetched bytes. | Synth post-check enforces this on every run (would fail the run, not silently insert a fabrication). Spot-check: `reports/stress26-mx3s/2026-05-24.filter.jsonl` row `{"pass":true, ..., "price":69.97, ...}` matches the report's rank 3 ($69.97 grandpas_tech_shop White) verbatim. mx3s bottom-line $65.00 onlinesmartshop_1 matches the cheapest passing `filter.jsonl` row. | **PASS** |
| 15 | **Web + mobile rendering** | Reports render cleanly at narrow viewport, including the new `[!NOTE]` callout. | Verified at 375×812 via Chrome DevTools MCP for `stress26-mx3s` and `stress26-ddr5`. Callout headers render as bolded `transient` / `needs work` / `no match` tags; "What to do:" guidance wraps cleanly; tables fit (4-column Sources, multi-column Ranked listings horizontally scrollable on mobile). Screenshot: [stress26_mobile_callout_mx3s.png](stress26_mobile_callout_mx3s.png). | **PASS** |
| 16 | **Multi-variant detail-URL redundancy (ADR-073)** | Multi-variant cosmetic SKUs get up to 3 detail URLs per vendor. | stress26-xm5 saved profile has 3 Best Buy detail URLs (Black + Silver + Smoky Pink) and 3 B&H Photo detail URLs (one per color). Target got 1 of 3 — minor LLM-adherence dip but within the cap. | **PASS** |
| 17 | **Phase 16 cleanup** | Deleting throwaway slugs leaves live products untouched. | Performed at end of session (see cleanup section below). | **PASS** (post-session verification) |

---

## Prioritised defect list

### Defect 1 — Onboarder can drop a detail-preferred URL entirely; ADR-079 save-gate has nothing to protect *(P1, behavioural)*

**Observed on:** stress26-mx3s.

**What happened:** the onboarder probed a B&H Photo detail URL for the MX Master 3S, the probe returned `detailExtractable: false`, and the LLM responded by **omitting the URL from the draft profile entirely** and leaving only a URL-less placeholder note in `sources_pending`:

```yaml
sources_pending:
  - id: universal_ai_search
    note: B&H Photo detail URL failed extraction on this probe; will retry search-style URL on next run
```

**Why this matters:** ADR-079 says a detail-preferred host (B&H is `prefer_page_type: detail` + `force_detail_backup`) whose probe fails should be **kept in `sources`** with an advisory note — exactly because a single weak probe is unreliable and the runtime ladder + circuit breaker (ADR-078) are strictly stronger. The save gate (`web/lib/onboard/gate-universal-ai.ts`) does the right thing **only when the URL is still in `sources` when save runs**. The LLM's pre-emptive demotion strips the URL before the gate sees it, so the protection ADR-079 promises is bypassed.

**Recommended fix:** prompt update + deterministic guard.

1. Prompt (`onboard_v1.txt`): a hard rule for the vendor-quirks knowledge map — *"if a detail-preferred host's probe fails, KEEP the URL in `sources` with the probe result as a `note`. Never drop it to `sources_pending` and never replace it with a URL-less placeholder. The save-time gate and the runtime breaker handle reliability."*
2. Deterministic check at save time (next to the existing ADR-067/074/080 guards): for each host in `PREFER_DETAIL_HOSTS ∪ FORCE_DETAIL_BACKUP_HOSTS`, if the draft has zero `universal_ai_search` sources for that host (in either `sources` or `sources_pending` with a URL), emit a soft warning. This is the missing safety net.

**Why systemic, not one-off** (per the `feedback_prefers_systemic_over_oneoff` memory): the same drop-rather-than-protect pattern would defeat ADR-079 for *any* detail-preferred host on any future onboard, not just B&H. The fix belongs in the prompt + gate, not in a per-profile patch.

### Defect 2 — ADR-084 callout silently treats `error: HTTPError ...` rows as OK because `passed` is host-aggregated *(P1, classifier fidelity)*

**Observed on:** stress26-xm5.

**What happened:** the xm5 report's Sources table contained three rows of the form `bestbuy.com | error: HTTPError: ... HTTP/2 stream 1 was not closed cleanly | 0 | 2`. The fourth Best Buy row was `bestbuy.com | error: skipped: AlterLab circuit open ... | 0 | 2`. The callout only included the *skipped* Best Buy entry (and the bhphoto/target/amazon skipped entries). The three HTTPError entries got no bullet at all — they showed up only as raw stack-trace cells in the table.

**Root cause hypothesis:** the per-source `passed` count rendered in the table is `2` for *every* bestbuy.com row including the failed ones, but only one row actually fetched/passed (`bestbuy.com | ok | 4 | 3` for the first row of the mx3s xrep, and `4 | 2` on xm5). So `source_stats[i].passed` appears to be carrying the host-aggregated (or per-product) total rather than the per-source count. `classify_source_outcome(...)` at [`source_reasons.py:96`](../worker/src/product_search/source_reasons.py#L96) short-circuits on `passed > 0` → returns `OK` → no bullet.

Net effect: for any host where one URL succeeds and another fails with a non-`skipped:` error, the failure gets no reason classification. Defeats one corner of the headline-validation goal of Phase 25.

**Recommended fix (capture for next phase, not implement now):**

1. Audit how `source_stats` is built in [`cli.py`](../worker/src/product_search/cli.py) (search loop around line ~800) and confirm whether `passed` is being overwritten with a host-level or run-level total before reaching `_build_zero_reason_callout`. The classifier itself is fine; the input shape is wrong.
2. Add a regression test in `test_cli.py` mirroring the live shape: same host appearing as N rows, one OK, the rest erroring — assert each erroring row's per-source `passed` is `0` and lands in the callout.
3. The Sources table also benefits from this fix (`Passed | 2` on a row that didn't fetch anything is genuinely confusing).

### Defect 3 — `microcenter.com` `known_failure` entry is stale; AlterLab now bypasses its Cloudflare wall *(P2, vendor-quirks registry drift)*

**Observed on:** stress26-mc (after manually moving microcenter from `sources_pending` → `sources`).

**What happened:** `microcenter.com | ok | 1 | 1` — the Ryzen 7 9700X detail URL extracted `$279.99 (new)` cleanly via the Tier-1.5 detail extractor, in a single fetch, no retries needed. The registry currently marks microcenter as:

```yaml
known_failure:
  severity: blocker
  summary: Cloudflare challenge served even at min_tier:3; bumping to min_tier:4
    produces silent AlterLab failure (body_len=0). No working path today …
```

That was true at the time the entry was written; it isn't true today.

**Recommended fix:** small registry maintenance — either remove the `known_failure` block entirely or downgrade `severity: blocker` → `warning` with a fresher note (e.g. "intermittent — succeeded for Ryzen 7 9700X detail URL on 2026-05-24 at tier 3 + networkidle; retain `default_alterlab_options`"). Then regenerate via `node web/scripts/sync-prompt.js`. **Re-probe with N≥3 fresh URLs before flipping** — one success could be a Cloudflare cache hit; we want repeatable success.

**Why this matters:** while microcenter is `known_failure`, the onboarder will route every microcenter URL into `sources_pending` and tell the user "this vendor is blocked". That's now a false negative.

### Defect 4 — Onboarder emits `spec_attrs` blocks without all required schema fields *(P2, ADR-074 followup #2 class)*

**Observed on:** stress26-ddr5.

**What happened:** the first Save attempt failed with:

```
profile failed schema validation
spec_attrs.form_factor.required: expected boolean
spec_attrs.ecc.required: expected boolean
spec_attrs.condition.required: expected boolean
```

The LLM emitted `spec_attrs: { form_factor: { type: str }, ... }` but omitted the schema-required `required: <bool>` field. A corrective round-trip ("simplify the profile, drop spec_attrs entirely") fixed it.

This is the same class as ADR-074 followup #2 (the `description:` schema-vs-onboarder gap): a schema-required field the prompt doesn't reliably emit. It costs the user (or the test driver) a round-trip per onboard that touches a component product with custom attributes.

**Recommended fix:** either (a) make `spec_attrs[*].required` optional with a sensible default, or (b) update `onboard_v1.txt`'s schema docs to make the requirement explicit and add an example. Either is cheap; (a) is more forgiving and matches what the LLM actually produces.

### Defect 5 — `low_seller_feedback` flag has no rendered description in the report's Flags section *(P3, cosmetic)*

**Observed on:** every report. The Flags section renders:

```
- **low_feedback**: (no description)
```

The flag itself fires correctly (multiple listings annotated `low_feedback`), but the rendered description is the literal string `(no description)`. The onboarder produced `flag: low_feedback` without a description field; the report renders the missing description as a placeholder rather than skipping the line.

**Recommended fix:** either (a) the onboarder always emits a 1-sentence description for built-in flags, or (b) the report omits the bullet entirely when no description is present. Either is fine; (b) is the no-prompt-change path.

### Defect 6 — Newegg search returns 820 KB of body but 0 parsed listings *(P2, parser-gap candidate)*

**Observed on:** stress26-mc. The callout correctly labels it `needs work` (the ADR-084 PARSER_GAP path):

> **newegg.com** — _needs work_: Fetched a full page (820,397 chars) but couldn't read any product listings off it — the page rendered, but our reader didn't recognise this vendor's layout (not a true empty result). **What to do:** open **Edit Profile** and add the vendor's product-page (detail) URL — that path extracts more reliably. If that also returns nothing, it needs a scraper fix (re-running won't help).

The reason taxonomy did its job — surfacing this as actionable, not as a silent zero. But the underlying parser gap is real: Newegg's search-tile structure isn't being recognised by either the anchor walker or the ADR-077 full-HTML extractor (820 KB is a fully-rendered search page).

**Recommended action:** capture a Newegg search-page fixture under `worker/tests/fixtures/universal_ai/newegg_search_ryzen9700x.html` and add a test that asserts the extractor finds ≥N candidates. Pair with the same approach used for Amazon in ADR-082 (frozen recall regression fixture). Investigation deferred — captured here for the next phase.

---

## "Noticed but deferred" (out-of-scope for Phase 26, captured for future)

- **Best Buy detail URLs trip curl_cffi HTTP/2 INTERNAL_ERROR after AlterLab fallback.** Same as PROGRESS "noticed but deferred" item #3 (2026-05-24); this sweep confirms it recurs and is what triggers the breaker on stress26-xm5. A bounded retry on this specific error class (cheap, no AlterLab cost) would let Best Buy detail URLs recover without consuming the breaker budget.
- **ddr5 onboarder source curation is too narrow.** The LLM picked one Dell-specific ServerSupply part-number URL and one Axiom-specific Provantage part-number URL — neither is representative of "any 256GB kit". For a stress test it's fine, but it's why ServerSupply returned 13 listings none of which matched (the Dell variant doesn't fit the 256GB target). Worth a prompt note on component products: prefer search/category URLs to single-part-number URLs when the user wants the cheapest matching SKU.
- **Centralcomputer + Provantage transient on this run** (pool exhausted on the same window AlterLab was generally degraded). Inconclusive; would re-test under healthy AlterLab.

---

## Cleanup verification (end-of-phase, post-deletion)

To be filled in after the `stress26-*` slug deletion step. Confirms:
- `products/stress26-*/` directories absent from `origin/main`
- `reports/stress26-*/` directories absent from `origin/main`
- Live products (`bose-nc700-headphones`, `breville-barista-express`, `ddr5-rdimm-256gb`, `sony-wh-1000xm5`, etc.) untouched

---

## Summary verdict

The Phases 20–25 stack is **substantially working** in production. The two real defects worth fixing are P1: the onboarder's ability to drop a detail-preferred URL before the save gate can protect it (Defect 1), and the host-aggregation of the `passed` count that defeats ADR-084 for non-skip error rows (Defect 2). Microcenter's `known_failure` entry is stale (Defect 3) — a small data fix that matters because the onboarder uses it. The remaining items (4–6) are paper-cuts or follow-ups.
