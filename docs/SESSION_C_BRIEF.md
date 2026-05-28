# Session C brief — Phase 29 recall + correctness follow-up after ADR-114/115 live test

> **Audience**: a fresh coding-agent session. Be literal. The user's intent is **systemic recall improvements that help future products**, not a one-off patch to the DJI Neo 2 profile. The DJI run is just the test bench.
>
> **Why this brief exists**: 2026-05-28 the user ran a live ADR-114/115 verification by re-onboarding `dji-neo-2-motion-fly-more-combo` against prod and then running it. ADR-114 + ADR-115 themselves *worked* (the modal appeared, the draft pane stayed in sync, the save eventually succeeded with 8 sources). But the test exposed **six new defects** in the layers around them — recall plumbing, onboarder rule-adherence, vendor-condition gating, and modal UX. The user asked for a comprehensive plan for the next session to tackle.

## Before you do anything

1. Read these in order:
   - `docs/PROGRESS.md` (state)
   - `docs/SESSION_PROTOCOL.md` (rules)
   - This file
   - `docs/DECISIONS.md` PROPOSED entries **ADR-116 through ADR-120** (remaining defects); ADR-121 is ACCEPTED/DONE — skip its body
   - For background: `ADR-098, ADR-099, ADR-101, ADR-105, ADR-109, ADR-111, ADR-115` — skim, don't memorize.
2. `git fetch origin && git pull --rebase --autostash origin main`. The app rewrites `products/**` + `reports/**` between sessions.
3. Confirm green at HEAD: `cd worker && python -m pytest -q` (expect 412+); `cd web && npm run lint && npm run test:guards && npm run test:parity && npx tsc --noEmit && npm run build`.

## The receipts (read once, then use as ground truth)

The live run that surfaced the defects:
- Profile committed by the app: `products/dji-neo-2-motion-fly-more-combo/profile.yaml` (commit `238509a`)
- Report committed by the app: `reports/dji-neo-2-motion-fly-more-combo/2026-05-28.{json,md,filter.jsonl}` (commit `e4c0ec9`)

The signal in the filter log:
- **eBay** 50 fetched, 23 passed — workhorse, fine.
- **B&H photo** 1 fetched, 1 passed (the LLM-picked detail URL).
- **Amazon search** 0 fetched (Defect D — `&i=toys-and-games` department filter hid drones).
- **Amazon detail URL** 1 fetched, 1 passed BUT IT IS THE WRONG PRODUCT — `amazon.com/.../dp/B0FJ1QH15P` is the DJI **Transmission Transceiver**, not the Neo 2. The hallucinated ASIN was also written into `match_aliases` (`profile.yaml:89`). **Defect A.**
- **Microcenter** 24 fetched, 0 passed — Microcenter returned Potensic ATOMs, Thrustmaster joysticks, even 3D-printing PLA on a clean `Ntt=` keyword search. Vendor genuinely doesn't carry the product but fell back to fuzzy garbage. ADR-109 fired "mis-scoped URL" — wrong diagnosis. **Defect E.**
- **Backmarket** 31 fetched, 0 passed — ALL rejected by `[condition_in] condition 'refurbished' not in ['new']`. Backmarket is refurb-only by design; was structurally guaranteed to contribute zero from the moment the onboarder added it. **Defect C.**
- **Target** 0 fetched (force_detail_backup host with only a search URL; backfill ran out of budget).

The product-naming wrinkle that drove some "missed" listings:
- Profile is locked to `DJI Neo 2 **Motion** Fly More Combo` (~$600 SKU w/ Goggles N3 + RC Motion 3).
- The Backmarket + Microcenter URLs the user cited are titled `DJI Neo 2 Fly More Combo` (no "Motion") — different/cheaper SKU (~$300-400). The filter is reading display_name + description as strict variant requirements and rejecting siblings. **Defect B.**
- User's principle (verbatim 2026-05-28): *"if the user doesn't specify which version of a SKU they want, all SKUs that meet the description they provided should be included."*

## The six defects — ordered by priority

Each maps 1:1 to a PROPOSED ADR. Pick them up in this order. **Do not try to do all six in one PR.** Commit after each ADR. Session protocol allows multiple ADRs in one session IF each is independently shippable and committed.

---

### Defect A — Detail-URL probe has no relevance check (ADR-116, **P0**)

**Evidence**: `profile.yaml:30` carries `amazon.com/DJI-Transmission-Transceiver-Beginners-Batteries/dp/B0FJ1QH15P` as a `page_type: detail` source — and `profile.yaml:89` lists `B0FJ1QH15P` in `match_aliases`. Both are the wrong product. The save-time probe modal showed ✓ ok on this URL.

**Root cause** (already located): `web/lib/onboard/probe-url.ts:745` — for `pageType === 'detail'`, `relevanceHits` is hard-coded to 0 and never checked. `extractDetailListing(html, url)` (line 581) only asks "can we pull a verbatim-verified price?" — never "is this product the requested product?" So any Amazon product page with extractable JSON-LD passes.

**Why it's P0**: the wrong-product URL is the most user-visible failure mode (the modal proudly shows ✓ on something that's clearly wrong). Worse, the LLM auto-promoted the bogus ASIN to `match_aliases`, which means runtime ai_filter would PASS any future listing that happened to contain `B0FJ1QH15P` (low-probability but high-blast-radius). Closes Standing Candidates #5 + #7 in PROGRESS.md.

**Files to touch**:
- `web/lib/onboard/probe-url.ts` —
  - Extend `extractDetailListing(html, url)` to `extractDetailListing(html, url, targetName)`. Have the Haiku call also return `{ titleMatch: boolean, extractedTitle: string }`. The probe result gains a `detailTitleMatch: boolean | null` field (null when not a detail probe, false when title doesn't carry the family-root tokens).
  - In `fetchUrl` / wherever probe results are returned, set `ok = false` + `reason = "detail page is for a different product (title: '...')"` when `pageType === 'detail'` and `detailTitleMatch === false`.
  - Update `ProbeResult` type accordingly.
- `web/app/api/onboard/probe/route.ts` — surface the new failure reason in the `url_done` SSE event (already passes through the probe's `reason`, just needs to render visibly).
- `web/app/onboard/OnboardChat.tsx` — modal renders the failure reason text (already does for non-detail probes; verify it does for detail too).
- `web/lib/onboard/validation.ts` — new save-time guard `checkMatchAliasesAgainstHallucinatedSkus`: walk every `universal_ai_search` source with `page_type: detail` and a recently-added `backfilled_from: save_time_probe` or LLM-added marker; if the URL contains a SKU-pattern token (e.g. `/dp/[A-Z0-9]{10}` for Amazon, `/c/product/\d+-REG/` for B&H, etc.) and that exact token appears in `match_aliases`, reject the save with a clear message telling the LLM the alias came from an unverified URL.
- `web/lib/onboard/check-onboard-guards.test.mjs` — new test cases for both gates.

**Fixture to capture** (cheap — one Anthropic call):
- Save the DJI Transmission Transceiver page HTML + the DJI Neo 2 B&H page HTML as fixtures under `worker/tests/fixtures/probe_url/` (already a folder? if not, create). Use them in the new TS unit test to assert that probing the Transceiver page with `targetName="DJI Neo 2 Motion Fly More Combo"` returns `ok: false`, while the B&H Neo 2 page returns `ok: true`.

**Done when**:
- Probing a real Amazon detail URL whose JSON-LD title does NOT match the family-root tokens of `displayName` returns `ok: false` with a meaningful reason.
- The new save-time alias-hallucination guard catches the exact `B0FJ1QH15P`-in-aliases pattern and rejects the save with a clear LLM-actionable error message (so ADR-113's auto-forward kicks in).
- `web tsc / eslint / test:guards / test:parity / next build` all green; new fixture-backed regression test passes.
- Update onboarder prompt (`worker/src/product_search/onboarding/prompts/onboard_v1.txt`) with one short rule: "Never add an ASIN/MPN/SKU pattern to `match_aliases` unless it came from a probed page whose `detailTitleMatch === true`." Run `node web/scripts/sync-prompt.cjs`.

---

### Defect B — Filter is too narrow; reject siblings by default (ADR-117, **P0 by impact, needs design interview first**)

**Evidence**: `reports/dji-neo-2-motion-fly-more-combo/2026-05-28.filter.jsonl` — many rejections of plausible Neo 2 listings:
- Line 3: `"DJI Neo 2 4K Drone Fly More Combo with RC Motion 3 Remote Controller"` — rejected because "no Goggles N3"; price $599 is in-band.
- Line 14: `"DJI Neo 2 Fly More Combo Goggles N3 RC Motion3 Gesture Control"` — PASSED (good).
- Lines 5/7/10/37/etc. — every `DJI Neo 2 (Drone Only) / DJI Neo 2 Fly More Combo` multi-variant eBay listing rejected.
- Line 76: the user-cited Backmarket `Drone DJI Neo 2 Fly More Combo` — would have been a "wrong-variant" rejection too (rejected on condition first).

**User's stated principle** (this is the design north star): *"if the user doesn't specify which version of a SKU they want, all SKUs that meet the description they provided should be included."*

**Why it's both P0 and design-first**: this is the **biggest pure-recall lever** in the entire system but it's a deliberate behavior change to the ai_filter (which has been tight on purpose since Phase 5). Don't just loosen the prompt and ship — talk to the user first. There are real trade-offs:
- A profile for a $179 1-year magazine subscription does NOT want sibling single-issue $4 SKUs included.
- A profile for `DDR5 256GB RDIMM 4800` does NOT want random DDR5 RAM included.
- BUT a profile for `DJI Neo 2 Motion Fly More Combo` arguably DOES want the regular `DJI Neo 2 Fly More Combo` because the user said "Neo 2 Fly More Combo" in their input.

**Recommended approach**:
1. **Open with an `AskUserQuestion` interview** before coding. The user explicitly said they like the multi-round interview pattern (memory: `feedback_interview_before_ux_work`). Likely axes:
   - Default behavior: family-match (lenient) vs strict-variant (current). User has already signaled lenient.
   - Opt-out mechanism: a profile-level `variant_strict: true` field for cases where the user really does want one SKU only (subscriptions, specific RAM SKUs).
   - How to derive the "family root": deterministic token extraction from `display_name` + `match_aliases` (similar to ADR-099's `_model_family_token`), or LLM-derived during onboarding and stored as a new profile field.
   - What signals MUST exclude a listing even under lenient mode: e.g. explicit `Drone Only` token, explicit "accessories", clear price-floor violation.
2. After the interview, write the ADR-117 body with the decision; then implement.

**Files likely to touch** (post-interview):
- `worker/src/product_search/validators/ai_filter.py` — the relevance-check prompt. Currently the prompt reads `display_name` + `description` as strict requirements. New behavior: derive a `family_root` (a deterministic token set) from profile, pass it as the relevance bar; treat full display_name match as a confidence boost, not a gate.
- `worker/src/product_search/profile.py` — new optional field `variant_strict: bool = False` (default lenient).
- `web/lib/onboard/schema.ts` — mirror the new field.
- `worker/src/product_search/onboarding/prompts/onboard_v1.txt` — onboarder rule: when the user is ambiguous about variant, leave `variant_strict` unset (defaults False = lenient); when the user explicitly names a specific bundle/SKU, set it True.
- Regression: re-run the DJI filter log against the new prompt offline (the .filter.jsonl rows are reusable as test cases — feed the title + price back into ai_filter via the fixture harness).

**Done when**:
- The interview is resolved + ADR-117 ACCEPTED with the chosen design.
- ai_filter under lenient mode passes the DJI multi-variant eBay listings and the regular `Fly More Combo` listings; ai_filter under `variant_strict: true` (or against a tight profile like the-week-1yr-subscription) still rejects them.
- No regression on existing tight profiles: the-week-1yr-subscription, ddr5-rdimm-256gb (if fixture exists), and supermicro-h14ssl-n family.
- Worker + web green; new tests pin both modes.

---

### Defect C — Vendor-condition compatibility gate (ADR-118, **P1, cheap registry change**)

**Evidence**: every Backmarket listing in the run was rejected with `[condition_in] condition 'refurbished' not in ['new']`. The profile's `condition_in: [new]` makes Backmarket structurally unable to contribute. Adding it cost a full Scrappey scrape every run for guaranteed-zero output.

**Root cause**: `vendor_quirks.yaml` knows about Backmarket's CF wall + Scrappey routing but doesn't encode the fundamental fact that **Backmarket's inventory is exclusively refurbished**. The onboarder has no way to know it shouldn't route Backmarket to a `condition_in: [new]` profile.

**Files to touch**:
- `worker/src/product_search/vendor_quirks.yaml` — add an optional `inventory_condition: refurbished` field to Backmarket. Future candidates (audit pass): Amazon Renewed, ServerSupply (some inventory), eBay used-only sellers. Document the field in the file's header comment.
- `worker/src/product_search/vendor_quirks.py` — load the new field; expose `inventory_condition_for(host)`.
- `web/scripts/sync-prompt.cjs` — emit `INVENTORY_CONDITION_BY_HOST` constant into `web/lib/onboard/vendor-quirks-data.ts`.
- `web/lib/onboard/validation.ts` — new save-time check `checkVendorConditionCompatibility`: for each `universal_ai_search` source, look up host's `inventory_condition`; if set and disjoint with profile's `condition_in` (e.g. vendor sells only `refurbished` and profile demands `new` only), reject with: `"backmarket.com only sells refurbished items, but condition_in is [new]. Either add 'refurbished' to condition_in, or move this vendor to sources_pending."`
- `worker/src/product_search/onboarding/prompts/onboard_v1.txt` — short rule: "Check `INVENTORY_CONDITION_BY_HOST` before adding a vendor. If the vendor's inventory condition doesn't overlap with the user's `condition_in`, route to `sources_pending` with a clear note."
- `web/lib/onboard/check-onboard-guards.test.mjs` — guard test for the new check.

**Done when**:
- Backmarket has `inventory_condition: refurbished` in the registry.
- The save-time gate rejects a draft with Backmarket as `universal_ai_search` + `condition_in: [new]`, surfacing the actionable error.
- Onboarder prompt regenerated; sync-prompt + parity green.
- Worker + web green; new guard test added.

---

### Defect D — Amazon `&i=<department>` department-restriction guard (ADR-119, **P1, niche but easy**)

**Evidence**: `profile.yaml:23` = `amazon.com/s?k=DJI+Neo+2+Motion+Fly+More+Combo&i=toys-and-games`. Drones live under Amazon's `electronics`/`photo` departments; `toys-and-games` hides them. The save-time probe reported `amazon.com — NO_MATCH (Vendor doesn't carry)` — technically correct for that URL, but misleading: Amazon DOES carry the product, the URL just locked to the wrong department.

**Root cause**: ADR-105 (registry-driven search URLs) doesn't currently restrict the LLM from APPENDING param suffixes after the template renders. The LLM saw a hint somewhere (web_search result, user statement, training prior) that drones are "toys" and added the filter.

**Files to touch**:
- `worker/src/product_search/vendor_quirks.yaml` — Amazon `search_url_notes`: add a hard rule: "NEVER append `&i=<department>` to the search URL — it restricts to one Amazon department and hides legitimate results. The unrestricted search (`/s?k=...`) is always correct."
- `web/lib/onboard/search-url-shared.ts` (or wherever URL canonicalization lives) — new function `canonicalizeAmazonSearchUrl(url)` that strips `i=` and `node=` query params. Apply automatically at save-time for any `amazon.com/s?...` URL.
- `web/lib/onboard/validation.ts` — soft warning if `node=` is stripped (could be intentional in rare cases — Books/Kindle/etc.); hard rejection for `i=` because there's no legitimate reason in this product domain.
- `worker/src/product_search/adapters/universal_ai.py` — defense in depth: same canonicalization applied at runtime so an old/migrated profile doesn't waste a scrape on a bad URL.
- `worker/src/product_search/onboarding/prompts/onboard_v1.txt` — the rule is now derived from the registry; re-run `sync-prompt.cjs`.
- Tests: parametrized URL canonicalization test (in `search_url/cases.json` if that's the right fixture location) + guard test.

**Done when**:
- Saving a profile with `amazon.com/s?...&i=anything` strips the param (or rejects, per interview); runtime canonicalizes too.
- DJI profile re-onboard cycle now produces a clean unrestricted Amazon search.
- Worker + web green.

---

### Defect E — "Vendor doesn't carry" vs "mis-scoped URL" diagnostic precision (ADR-120, **P2**)

**Evidence**: Microcenter returned `Potensic ATOM 2`, `Thrustmaster Hotas Warthog`, `FLOURESCENT YELLOW PLA`, etc. — items with **zero overlap with the requested brand**. The URL (`Ntt=DJI+Neo+2+Motion+Fly+More+Combo`) is correct per ADR-105; Microcenter just doesn't carry the Neo 2 and fell back to fuzzy/category results. ADR-109's mis-scope diagnostic fired anyway — wrong diagnosis, will mislead the user to "fix" a fine URL.

**Root cause**: `cli.py:annotate_dominant_rejections` keys on `relevance_check` being the top rejection reason but doesn't look at WHAT kind of relevance failure. A relevance rejection where the title shares zero brand/category tokens with the target = "vendor doesn't carry"; a relevance rejection where the title is in the same family but wrong variant = "wrong variant on the page" (covered by ADR-117); a relevance rejection where titles are a different category entirely (Thrustmaster joysticks for a drone query) = "vendor doesn't carry, search returned fallback junk".

**Files to touch**:
- `worker/src/product_search/cli.py` — `annotate_dominant_rejections`: when computing the dominant rejection, also compute a `dominant_rejection_subkind` from the per-rejection title overlap with the target's family-root tokens + brand. Three sub-kinds:
  - `wrong_variant` — titles share family-root tokens but lack variant tokens (e.g. "Fly More Combo" without "Motion").
  - `vendor_doesnt_carry` — titles share zero brand/family-root tokens (Thrustmaster on a DJI search).
  - `mis_scoped` — titles span many unrelated brands AND the URL contains a category-node param (`fq=brand`, `N=<digits>`, etc.). This is the existing trigger.
- `worker/src/product_search/source_reasons.py` — outcome message map gets two new variants per subkind.
- `web/lib/onboard/report-types.ts` (or wherever) — render the new subkinds with appropriate user-facing wording. Existing wording: "search URL may be mis-scoped" stays for `mis_scoped`; new wording for the others.
- Regression fixture: reuse `reports/dji-neo-2-motion-fly-more-combo/2026-05-28.filter.jsonl` Microcenter section as the `vendor_doesnt_carry` regression — assert the new subkind fires.

**Done when**:
- Microcenter's DJI case classifies as `vendor_doesnt_carry`, not `mis_scoped`.
- A prior `microcenter_dji_brand_misscope.html` fixture (if present from Phase 29 capture) still classifies as `mis_scoped` (`fq=brand:DJI`).
- The eBay/Walmart wrong-variant case (when ADR-117 lenient-mode ships) classifies as `wrong_variant`.
- Worker + web green.

---

### Defect F — Save-time probe modal: non-terminating loop + row proliferation (ADR-121, **DONE 2026-05-28 Session C**)

> **2026-05-28 (Session C review by the next agent):** the user re-flagged this defect and explicitly said the original brief did NOT address (1) the "very confusing apparent proliferation of target URL probes" or (2) "whether or not I was stuck in a loop that would have gone on forever." Investigation confirmed **both are real bugs**, not UI confusion. The original P3-cosmetic framing was wrong. Rewriting the section with the actual root causes found in code.

**Evidence**: user's verbatim feedback: *"it kept adding more searches for no reason I could see every time I asked it spend more time probing — I wondered if that cycle would just last forever."* Screenshots: the `DETAIL-URL BACKFILL` section grows 1 → 2 → 4 target.com rows across attempts 2-4; every attempt shows the **same** 4 URLs (amazon search, microcenter, backmarket, target) as "budget exhausted before this URL was probed." The header just says "Probe attempt N."

**Root cause #1 — the loop genuinely never terminates (the real bug behind "would it last forever?").**
- `web/app/api/onboard/probe/route.ts:29` sets `PROBE_BUDGET_MS = 45_000`. Line 365 launches every probe in parallel, raced against that deadline (`Promise.race([Promise.all(probeTasks), deadlinePromise])`, line 389).
- The Cloudflare-walled vendors (microcenter + backmarket route through Scrappey; target + amazon are slow too) each take **longer than 45s** to fetch+render. When the deadline fires, *none* of them have resolved, so all four are pushed onto `unprobed` (line 392-398).
- `onContinueProbe` (`OnboardChat.tsx:588`) re-probes exactly that `unprobed` set with a **fresh 45s budget** → identical timeout → identical 4 still unprobed. The set never shrinks. **There is no zero-progress detection and no attempt cap.** The only escape is "Save and proceed anyway." The user's fear was correct: for slow/walled vendors this loop is infinite.

**Root cause #2 — the visual "proliferation" of target.com rows.**
- The server runs the ADR-076 backfill phase on **every** attempt (line 419), NOT gated to unprobed URLs. For a `force_detail_backup` host with a search URL but no detail URL (target.com), it always reaches the `Date.now() >= deadlineMs` branch (line 420) — the probe phase already ate the whole budget — and emits one more `backfill_skip` "budget exhausted before backfill could run."
- The client carries `probeState.backfills` forward into the next attempt (`onContinueProbe`, `OnboardChat.tsx:596`) and `streamProbe` seeds `backfills = [...carryOver.backfills]` (line 463), then **appends** the new attempt's skip rows (line 527-533) with no de-dup by host. So target.com accumulates one more identical row per attempt — exactly the 1→2→4 growth in the screenshots. No new searches are actually being added; it's a display accumulation bug.

**Files to touch**:
- `web/app/api/onboard/probe/route.ts` —
  - **Bound the loop.** Track per-URL whether *any* progress was made this attempt. If a `Continue` pass resolves with `unprobed` identical to the incoming `unprobed` (zero new URLs finished), emit `done` with a new `noProgress: true` flag so the client can stop offering plain "Continue."
  - Consider giving a single slow vendor a chance: either probe slow/walled hosts **sequentially with a per-URL soft cap** so at least one finishes per attempt, or raise the effective budget for a 1-URL continue pass. (Decide during implementation; the must-fix is that progress is possible or the loop is explicitly ended.)
  - Optionally emit a `plan_summary` SSE event at the start of each pass with `{ totalUrls, plannedBackfill, byHost }` so the modal can show totals.
- `web/app/onboard/OnboardChat.tsx` (probe modal section) —
  - **De-dup backfill rows by host** when carrying over (`onContinueProbe`) — replace the prior host row instead of appending, so target.com shows once, not N times.
  - Header: replace "Probe attempt N" with progress that shows work remaining and that the planned set is capped per host (`MAX_BACKFILL_PROBES_PER_HOST = 3`, ADR-076).
  - Per-host roll-up at top (e.g. "amazon.com 0/1 ⏳, target.com 0/1 ⏳").
  - Re-label "DETAIL-URL BACKFILL" → "Looking for product detail pages on hosts that only have a search URL" with a one-line `<details>` explainer.
  - **Hard cap / no-progress exit:** after a `noProgress: true` pass (or after 3 successive Continue clicks with no shrink in `unprobed`), replace the **Continue probing** button with "Stop and save what we have" (still ADR-111-gated, but the loop is now visibly bounded and can't run forever).

**Shipped 2026-05-28 (Session C, commit `10a46d2`)**. Server emits `noProgress: true` + `plan_summary` event; client hides Continue on no-progress and surfaces "Stop and save what we have"; backfill rows de-duped by host via `upsertBackfill`; per-host roll-up chips; header shows done/total. 4 new guard tests (35 total). Web tsc/eslint/test:guards 35/35/test:parity 6/6/next build green.

---

## Suggested pick-up order in the next session

If running with **claude-opus-4-7** on a normal session budget, the realistic shape is:

**ADR-121 shipped 2026-05-28 Session C** — the infinite-loop + row-proliferation bugs are fixed. Start fresh on ADR-116.

1. **ADR-116 (P0)** — detail-URL relevance gate + match_aliases hallucination guard. Narrow, fixture-backed, biggest remaining correctness win; closes two standing candidates. ~2-3 hours including fixture capture.
2. **ADR-118 (P1)** — vendor-condition compatibility gate (Backmarket refurb registry entry + save-time guard). Simple registry change. ~1 hour.
3. **ADR-119 (P1)** — Amazon `&i=<department>` URL strip at save + runtime canonicalization. URL canonicalization + warning. ~45 min.
4. **ADR-120 (P2)** — `vendor_doesnt_carry` vs `mis_scoped` vs `wrong_variant` diagnostic sub-kinds. Touches `cli.py` only on the worker side. ~1 hour.

**ADR-117 (P0 by impact)** should be its own session because it starts with an `AskUserQuestion` interview, not coding. Open it AFTER 116/118/119/120/121 have shipped — that way the user has clean feedback signal on whether the lenient filter actually improves recall, vs other defects masking it.

## Hard rules (don't violate)

- **ADR-068**: every `vendor_quirks.yaml` change runs `node web/scripts/sync-prompt.cjs` afterwards. Verify `INVENTORY_CONDITION_BY_HOST` (or whatever new constant ADR-118 adds) shows up in `web/lib/onboard/vendor-quirks-data.ts`.
- **ADR-062**: never let a test depend on a live `products/<slug>/` entry. The DJI profile + report exist today but the app may rewrite or delete them. Use committed fixtures under `worker/tests/fixtures/` or new fixtures under `web/__fixtures__/` if needed.
- **ADR-001**: none of these defects touch the no-fabrication boundary, but be careful with ADR-116: the new `targetName` check passes through Haiku; the boolean it returns is allowed (Haiku judging title relevance is fine), but never let the page's title be FABRICATED — pull the literal extracted title from the HTML or JSON-LD, don't ask Haiku to invent one.
- **CLAUDE.md sync discipline**: rebase around your commits. The app may push between your work and your push.
- **Push pre-authorized** (CLAUDE.md standing authorization). Commit after each ADR; push at the end of the session or after every 1-2 commits.

## Out of scope for this brief

- Re-onboarding or "fixing" `products/dji-neo-2-motion-fly-more-combo/profile.yaml` directly. The profile is the test bench; once ADRs 116/118/119 ship, the next re-onboard cycle will produce a cleaner profile naturally. Per-profile patches violate the "systemic over one-off" rule the user has reinforced multiple times.
- The user-cited Backmarket / Microcenter URLs being "missed." Once ADR-118 ships, Backmarket is correctly routed to `sources_pending` instead of wasting a scrape. Once ADR-117 ships (separate session), the wrong-variant rejections become the lenient-mode user-visible question. Microcenter genuinely doesn't carry the Neo 2 — ADR-120 will at least label it honestly.
- ADR-114 / ADR-115 themselves — both shipped and verified live (the modal works, the draft pane stayed in sync per the 8-source save). No follow-up needed beyond ADR-121's cosmetic polish on the modal.
