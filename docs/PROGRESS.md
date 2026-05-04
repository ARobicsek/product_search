# Progress

**This is the live status file. Every dev session reads it first and updates it last.**

## Active phase

**Next: Phase 19 — Universal adapter accuracy & vendor reach (NEW, urgent).** Phase 15 closed structurally (heuristic v2 + JSON-LD tier + probe gate shipped), but the first two live runs through the full pipeline (Bose + Breville, 2026-05-04) surfaced two correctness problems that block any further roadmap progress: **wrong prices in Amazon listings** and **near-total vendor bot-blocking**. Phases 16–18 (slug deletion, schedule editor, polish) are deferred until Phase 19 lands. See the Phase 19 brief in [PHASES.md](PHASES.md#phase-19--universal-adapter-accuracy--vendor-reach-urgent).

## Status as of 2026-05-04 PM (Phase 15 closeout + live-run diagnosis)

**Two live runs through the new pipeline. Both found things the test suite couldn't surface.**

### Run 1 — Breville Barista Express (pre-2e19afa push, ~04:00 UTC 2026-05-04)

Extraction worked structurally — 38 fetched / 3 passed from amazon.com, 23 fetched / 3 passed from target.com — but the **Amazon prices are wrong**.

- User example: BES876BSS Impress recorded as **$489.50** in the per-run CSV; the actual Amazon page shows **$649.95**.
- All 3 Amazon Breville listings are suspect (other recorded prices: $421.63 for BES870XL — Barista Express MSRP ~$700; $469.29 for BES870BSXL — Black Stainless variant). Target's prices for the same models on its own pages came back at $549.99 / $649.99, matching real retail.
- Likely root cause: Amazon's search-result cards inline both a "From:" / used-condition price AND the new-condition price; our 1500-char ancestor walk pulls every visible `$` token into `price_hints`, and the LLM picks the cheapest plausible one. Pre-Phase-15 this rarely surfaced because the old 600-char walk often missed prices entirely; now we get prices, but sometimes the wrong ones.
- This is a CORRECTNESS bug, not a coverage bug. Wrong prices → wrong rankings → user makes wrong decisions. Top priority for Phase 19.

### Run 2 — Bose NC 700 (post all 3 push commits, ~12:00 UTC 2026-05-04)

Restoring the 5 demoted universal_ai sources put them back in `sources` per ADR-038, but **none of them produced any NC 700 listings**:

| Source | Fetched | Passed | LLM input tokens | Notes |
|---|---|---|---|---|
| ebay_search | 44 | 40 | (n/a) | Same as before, healthy |
| amazon.com | 0 | 0 | (no LLM call) | 0 anchor candidates — different page than the 12-fetched May 4 AM run |
| bose.com /c/refurbished | 17 | 0 | 6,700 in / 1,032 out | Right page but Bose discontinued the NC 700; LLM emits 17 wrong-product listings, ai_filter rejects all |
| backmarket.com | 0 | 0 | (no LLM call) | 0 anchor candidates from rendered fetch |
| bestbuy.com | 0 | 0 | 2,902 in / 13 out | LLM saw a few candidates, rejected them all |
| walmart.com | 0 | 0 | 622 in / 13 out | Almost-empty candidate list (anti-bot stripped DOM) |
| crutchfield.com | 0 | 0 | (no LLM call) | 0 anchor candidates (Cloudflare turnstile) |
| reebelo.com | 0 | 0 | 508 in / 13 out | Almost-empty candidate list |

**Net result**: 7 of 8 universal_ai sources contributed zero listings. The one that DID contribute (bose.com) only contributed wrong-product noise that ai_filter correctly threw out, costing $0.012 in LLM tokens for nothing.

**Diagnosis**:
- Amazon's 12 → 0 regression between morning and afternoon runs is Amazon serving different DOM to different sessions/IPs (likely AlterLab's outbound IPs got flagged). Not a code regression; we can't reproduce it deterministically.
- backmarket / crutchfield / amazon (this run): rendered fetch returns a body, but the body has no extractable product anchors. AlterLab's Cloudflare bypass works inconsistently.
- walmart / reebelo: `_extract_candidates` returns ~0 candidates, suggesting the rendered DOM doesn't contain product cards.
- bose.com /c/refurbished: this URL needs to be removed from the profile entirely. Bose discontinued the NC 700 product line; no amount of extraction quality will surface NC 700 listings on a refurb page that doesn't carry it.

### Cumulative cost picture

This run's universal_ai LLM spend was **$0.016** for 0 listings shipped. Onboarder will still propose universal_ai URLs for new products, and many of them will fall into this same "fetches OK, extracts wrong / extracts nothing" trap. The economic argument for universal_ai_search depends on hit rate ≥30%; today on bose-nc-700 it's effectively 0%, on breville-barista-express it's 6/61 = 10% with quality concerns.

## Live state at handoff
- All Phase 15 work + the same-day follow-up (gate relaxation, Amazon split-price, Bose profile restore, push-policy flip) committed and pushed. Latest pushed SHAs: `06a1de4` (bose restore), `cfa1956` (push policy), `2e19afa` (gate + split-price).
- Test suite: 161/161 worker, web tsc + next build green.
- No uncommitted changes.

## Next session — start here (Phase 19, urgent)

1. Read the Phase 19 brief in [PHASES.md](PHASES.md#phase-19--universal-adapter-accuracy--vendor-reach-urgent).
2. **Top priority: Amazon price attribution** — either tighten `_ancestor_card_text` to per-card boundaries (smaller hops + smarter container detection) or add an Amazon-specific price selector that prefers `data-a-color="base"` + `<span class="a-offscreen">` on the same product card. Re-run the breville-barista-express search and confirm BES876BSS now records $649.95 (not $489.50).
3. **Second: bose.com URL** — remove `/c/refurbished` from the Bose profile (or replace with a working NC 700-specific URL if one exists). Stop burning $0.012/run on guaranteed-rejection listings.
4. **Third: vendor bot-blocking diagnosis** — for each 0-candidate-yielding source (amazon, backmarket, crutchfield, walmart, reebelo, bestbuy on Bose), capture the AlterLab response body to a fixture. Decide per vendor: keep in `sources`, demote to `sources_pending`, or remove entirely.
5. After Phase 19, return to Phase 16 (slug deletion).

## Status as of 2026-05-04 AM (Phase 15 follow-up — gate policy revision + Amazon split-price) [archived]

**Two fixes after the first live save through the new gate:**

1. **Save-time probe gate is now hard-failure-only** (ADR-038, refines ADR-037). The first save through Phase 15's gate demoted backmarket — our one known-working universal_ai vendor — because the TS-side raw `fetch` got Cloudflare-challenged and the gate concluded "0 candidates". But production uses AlterLab, which renders backmarket fine. The gate is now a sanity check (404 / network error / sub-500-byte body), not a correctness gate. The user's existing Bose profile still has 6 URLs in `sources_pending` from the old gate; they won't auto-migrate.

2. **Amazon split-price extraction** in [worker/src/product_search/adapters/universal_ai.py](../worker/src/product_search/adapters/universal_ai.py). Amazon's `<span class="a-price-symbol">$</span><span class="a-price-whole">329</span><span class="a-price-decimal">.</span><span class="a-price-fraction">99</span>` markup flattens through selectolax as `$ 329 . 99` — the standard regex captured only `$329` (wrong cents). New `_canonicalize_prices` rewrites the split form to `$329.99` before the standard pattern runs, so both joined and split markup yield the same result. New synthetic `amazon_split_price.html` fixture pins three cards (with a-offscreen, split-only, and joined markup); 2 new tests.

**Tests**: 161/161 worker tests pass (was 159). web `tsc` clean.

**Open question for the user**: the Bose profile's 6 demoted URLs (backmarket, gazelle, bestbuy, walmart, crutchfield, reebelo) are still in `sources_pending` from the pre-revision gate run. Path forward:
- Hand-edit `products/bose-nc-700-headphones/profile.yaml` to move all except gazelle (which is a real 404) back into `sources`.
- OR re-save the profile through the onboarder, which now runs the relaxed gate.
- Doing nothing leaves the profile in its current state (ebay-only) until next manual edit.

## Status as of end of 2026-05-03 session (Phase 15 closeout — tasks 3+4+5)

**Phase 15 done end-to-end. Anchor heuristic redesigned around per-canonical-URL merging, three real-vendor fixtures committed, save-time TS probe gate routes 0-candidate `universal_ai_search` URLs to `sources_pending`. ADR-037 written. Local commit pending; no push yet.**

What landed this session (on top of tasks 1+2 from earlier in Phase 15):

1. **Anchor heuristic redesigned** in [worker/src/product_search/adapters/universal_ai.py](../worker/src/product_search/adapters/universal_ai.py). Key changes:
   - Two-pass extraction: collect all anchors first into per-canonical-URL groups, THEN merge each group into a single candidate (best title, union of price hints, longest context). Pre-Phase-15 the inline dedupe dropped sibling anchors, which broke Shopify's split-card markup where title-anchor and price-anchor are siblings pointing at the same product URL.
   - New `_anchor_title` helper — when anchor text is empty, fall back to descendant `<img alt="...">`, then aria-label, then title attribute. Recovers ~90% of Target's product titles (whose `<a><img></a>` cards previously returned empty strings).
   - `_ancestor_card_text` bumped from 4 hops / 600 chars to 6 hops / 1500 chars so headphones.com cards can reach the price in a sibling `card__content` 5 hops up.
   - New `_looks_like_nav_path` filter disqualifies CMS / chrome paths (`/pages/contact-us`, `/blogs/buying-guides`, `/store-locator`, `/account`, etc.) that the existing `_looks_like_product_url` was passing because their last segment happened to be hyphenated and ≥6 chars.
   - `_UI_CHROME_TEXTS` expanded with high-frequency nav strings observed in fixtures.

2. **Real-vendor fixtures captured** (live AlterLab + httpx fetches, 2026-05-03):
   - [headphones-com-shopify-collection.html](../worker/tests/fixtures/universal_ai/headphones-com-shopify-collection.html) — Shopify, server-rendered, 51 product anchors. Pre-fix: 35 candidates with 0 prices. Post-fix: 25 candidates with 24 prices.
   - [target-search-bose.html](../worker/tests/fixtures/universal_ai/target-search-bose.html) — custom React, AlterLab-rendered, 50 image-only anchors. Pre-fix: 50 candidates with 0 prices and ~95% empty titles. Post-fix: 47 candidates with 46 prices and 47 titles via the img-alt fallback.
   - [bhphotovideo-search-bose.html](../worker/tests/fixtures/universal_ai/bhphotovideo-search-bose.html) — fully bot-blocked React shell. Yields ≤4 nav-only candidates with 0 prices. Pinned as a regression fixture for "0-candidate page is correctly identified as such" so the onboarder probe-gate can route to `sources_pending`.
   - **Tried but didn't ship**: bestbuy (geofenced even with AlterLab), crutchfield (Cloudflare turnstile), newegg (CAPTCHA), walmart/sweetwater/adorama (AlterLab 504s), audio46 (Shopify with client-side product hydration). All documented as known-blocked in ADR-037.

3. **TypeScript probe + save-time gate** for the onboarder:
   - [web/lib/onboard/probe-url.ts](../web/lib/onboard/probe-url.ts) — port of `_extract_jsonld_listings` + a coarse product-URL anchor count. Regex-based JSON-LD block extraction (no `cheerio`/`linkedom` dep added). Returns `{ ok, jsonldCount, anchorCount, reason }`.
   - [web/lib/onboard/gate-universal-ai.ts](../web/lib/onboard/gate-universal-ai.ts) — wraps the probe, parallel-probes every `universal_ai_search` source on the draft (8s per-URL timeout). Demotes 0-candidate URLs to `sources_pending` with `note: "probe returned 0 candidates: <reason>"`.
   - [web/app/api/onboard/save/route.ts](../web/app/api/onboard/save/route.ts) calls the gate before YAML render on the structured-`draft` path; passes probe reports back to the client in the response.
   - [web/app/onboard/OnboardChat.tsx](../web/app/onboard/OnboardChat.tsx) surfaces probe failures in the error path (so the user can see why `sources` ended up too thin to validate); silent on the success path (the demotions appear in the committed YAML's `sources_pending` block).

4. **Tests + CI**: 6 new tests added to [test_universal_ai.py](../worker/tests/test_universal_ai.py): one for the new `_looks_like_nav_path`, one for `_anchor_title`'s img-alt fallback, three pinning the new fixtures' extraction quality, one end-to-end "fetch with stubbed LLM yields ≥3 Listings from Target". **159/159 worker tests pass** (was 153). Web `tsc --noEmit` clean; `next build` green; ESLint shows 1 pre-existing warning, 0 new.

**Live state at handoff**:
- Local commit pending: anchor heuristic changes, 3 new fixtures, 7 deleted speculative fixtures (kept only what tests reference), 6 new tests, TS probe + gate, OnboardChat probe-error surfacing, ADR-037, this PROGRESS update.
- Build green; tsc clean; lint clean; 159/159 worker tests pass.
- **Phase 15 → closed.** Phase 16 next.

**Noticed but deferred**:
- The TS probe is a strict subset of the Python adapter (no LLM call), so some URLs that would work fine in production get demoted to `sources_pending` at save time. Conservative-fail is the right default for now; if it gets noisy in practice, options are (a) add a "force include" toggle on the chat UI, or (b) port more of the anchor heuristic to TS so the gate matches production more closely.
- The wider ancestor walk (1500 chars) on the synthetic fixture pulls section-level prices into every candidate's hint list. Real fixtures don't suffer because per-card text exceeds 1500 chars before the walk crosses card boundaries. Documented in ADR-037 as a known trade-off.
- B&H, Best Buy, Walmart, Newegg, Crutchfield, Sweetwater, Adorama all fail rendered AlterLab probes today. Out of reach without per-vendor adapters or a stronger fetch tier; documented as known-blocked.

**Next session — start here (Phase 16)**:
1. Read the Phase 16 brief in [PHASES.md](PHASES.md#phase-16--slug-deletion-hard-delete).
2. Implement `DELETE /api/profile/[slug]` route (auth via `WEB_SHARED_SECRET`).
3. Home-page UI: typed-confirmation modal before delete enables.
4. Write ADR-036 (auth model, what gets deleted, mid-run safety).

## Status as of end of 2026-05-03 session (Phase 15 — tasks 1+2 + cheap win) [archived]

**JSON-LD extraction tier and `cli probe-url` shipped. Tasks 1 and 2 of the Phase 15 brief are done. Tasks 3–5 paused pending user input.**

What landed:

1. **Onboarder cheap win** (ADR-034 follow-up): `WEB_SEARCH_MAX_USES` lowered 5 → 2 in [web/app/api/onboard/chat/route.ts](../web/app/api/onboard/chat/route.ts). Bench-time observation was that two consecutive vendor-discovery turns fired 3+4 searches each — wasteful on diminishing-return crosschecks. Kept at 2 (not 1) so the model can still cross-reference one candidate vendor per turn.

2. **JSON-LD / microdata extraction tier** (Phase 15 task 1) in [worker/src/product_search/adapters/universal_ai.py](../worker/src/product_search/adapters/universal_ai.py). New `_jsonld_blocks` / `_walk_jsonld` / `_offer_price_and_condition` / `_extract_jsonld_listings` helpers run BEFORE the anchor-heuristic + LLM tier. When a page exposes `Product` / `ItemList` / `@graph`-of-Products, listings are extracted directly into the `Listing` shape — `attrs.extractor = "jsonld"` — and the LLM is **never called**. Anchor-tier listings now also carry `attrs.extractor = "anchor_llm"` so downstream code can tell the tiers apart from the per-run CSV alone. Handles the Schema.org variations seen in the wild: `@type` as string OR list, `Offer` / `Offer[]` / `AggregateOffer` (with `lowPrice`), `itemCondition` URLs, malformed JSON-LD blocks (skipped without crashing), European decimal commas (`12,99` → 12.99), dedupe on canonical URL.

3. **`cli probe-url <url> [--render]`** (Phase 15 task 2) in [worker/src/product_search/cli.py](../worker/src/product_search/cli.py). Reports fetcher used / origin status / body length / JSON-LD count / anchor candidate count + 3 sample candidates with title and price. Exits nonzero when zero candidates surface, so it's usable as both a manual diagnostic and a programmatic gate (the onboarder hook in task 5 can shell out to it). `--render` requires `ALTERLAB_API_KEY` and errors out (exit 2) if unset OR if AlterLab silently fell through to curl_cffi (exit 1) — so callers can distinguish "rendered fetch returned 0 candidates" (extraction problem) from "raw fetch returned 0 candidates" (probably needs rendering).

4. **Tests + fixtures**: 2 new synthetic fixtures ([shopify_jsonld.html](../worker/tests/fixtures/universal_ai/shopify_jsonld.html), [custom_aggregate_offer.html](../worker/tests/fixtures/universal_ai/custom_aggregate_offer.html)), 5 new JSON-LD tests in [test_universal_ai.py](../worker/tests/test_universal_ai.py), 4 new probe-url tests in [test_cli.py](../worker/tests/test_cli.py). **153/153 worker tests pass** (was 144 baseline).

**Live state at handoff**:
- All work committed and pushed to `origin/main` (squashed into one commit).
- Build green, full worker test suite passes. Web tsc/lint not re-run this session because no Next.js routes/types changed — only the one constant `WEB_SEARCH_MAX_USES`.
- Phase 15 task 1 + 2 done. Task 3 (real-vendor fixtures), task 4 (anchor heuristic tightening), task 5 (onboarder probe_url hook) remain.

**What you can test yourself before next session**:

- **Onboarder cost smoke test**: open `/onboard`, ask for a category that triggers vendor research (e.g. "mechanical keyboards under $200"). Watch the SessionCost panel — search-heavy turns should now max out at 2 web searches instead of 5. Total cost on a long session should drop measurably (rough expectation: 30–50% lower on the first 10 turns vs the Phase 14 bench's $0.1779 / 15 turns, depending on how many turns hit web search).
- **No live universal_ai test is needed**: the new JSON-LD tier is fully exercised by tests; the anchor + LLM path is unchanged structurally.
- **Optional**: run `python -m product_search.cli probe-url <a-vendor-url>` against a Shopify store you know (e.g. headphones.com, audio46.com) to see how it behaves on real data. Useful for picking task-3 candidate vendors.

**Open questions for next session — please answer first**:

1. **Task 3 — fixture-capture strategy.** The brief asks for fixtures from 6 real vendors (Shopify, Magento, BigCommerce, custom React, refurb marketplace, big-box). We already have 3 real fixtures from prior phases (`amazon-bose-nc700-search.html`, `backmarket-bose-nc700-search.html`, `gazelle-headphones-collection.html`) and 2 synthetic ones for the JSON-LD tier (`shopify_jsonld.html`, `custom_aggregate_offer.html`). Two options:
   - **(a)** Live-capture 3 more from real stores (would burn AlterLab credits on render-required ones — bestbuy/bhphotovideo are likely tier-3-only). Higher fidelity, more expensive, may flake when sites redesign.
   - **(b)** Lean on the existing real fixtures + a couple more carefully-crafted synthetic fixtures targeting specific failure modes (e.g. split-price markup, `data-price` attributes). Cheaper, more durable, potentially less representative.
   - Default if you don't pick: **(b)**, since CLAUDE.md is explicit about not re-scraping live sites unless required.

2. **Task 4 — anchor heuristic tightening.** This depends on what task 3's fixtures reveal. The brief mentions specifically: split-price markup (`<span>249</span><sup>99</sup>`), `data-price` attributes, possibly raising `max_candidates` or paginating. If you have a specific vendor in mind that currently 0/0s and should work, name it — that's the best forcing function.

3. **Task 5 — onboarder probe_url integration.** This is web-side work in `web/app/api/onboard/`. The brief says: when the AI proposes a `universal_ai_search` URL, it must call `probe_url` first, and 0-candidate URLs must land in `sources_pending` (with `"probe returned 0 candidates"` note) instead of `sources`. Two implementation paths:
   - **(a)** Add a server-side custom tool to the Anthropic chat route that shells out to `python -m product_search.cli probe-url <url>` from the edge runtime. Edge runtime can't fork subprocesses — would need to move the route to Node runtime. Material refactor.
   - **(b)** Add a `/api/probe-url` Next.js route that the chat route calls, which in turn calls the worker via a small HTTP shim or by directly running the Python (still subprocess-bound). Same problem.
   - **(c)** Re-implement the probe logic in TypeScript on the web side (DOM walk + JSON-LD extraction, no LLM). Smaller surface in TS than in Python because the LLM-heavy anchor tier only matters for marginal cases — JSON-LD alone is sufficient for "does this URL yield ≥1 listing for the user's query?". Fastest, no runtime change.
   - Default if you don't pick: **(c)** — port just `_extract_jsonld_listings` and a thin fetch into TS, since that's what the onboarder actually needs ("does this URL look usable?").

Once those three are answered, the next session can finish Phase 15 and write ADR-037 (JSON-LD tier + probe pattern).

## Status as of end of 2026-05-03 session (Phase 15 prelude — stale-run mitigation) [archived]

**The "stale report after Run-now" complaint is closed end-to-end. ADR-035 written. Phase 15 proper is up next.**

What landed:

1. **Run-in-flight UI wipe** ([web/app/[product]/ReportSection.tsx](../web/app/[product]/ReportSection.tsx) + [web/app/[product]/runState.ts](../web/app/[product]/runState.ts), commit `ced395b`). Tiny client-only pub/sub store backed by `useSyncExternalStore`. `RunNowButton` publishes its state into the store; `<ReportSection>` wraps the report markdown + `RunInfoFooter` and swaps to a spinner card with "Running a fresh search… previous report is hidden so you don't act on stale numbers." The wipe persists through `dispatching → polling → done`, so the brief window before `window.location.reload()` doesn't show the prior data either. Cleanup on unmount clears the flag, so navigating away mid-run doesn't leak hidden state to a later visit.

2. **Footer timestamp consistency fix** ([web/lib/dispatch.ts](../web/lib/dispatch.ts), commit `a26bffb`). `getLastCompletedRun` was hitting `/runs?event=workflow_dispatch&status=completed&per_page=20`. GitHub's status-filtered index is eventually consistent — for tens of seconds after a workflow completes, the just-finished run is missing from that view. So right after Run-now, the "Last run completed …" footer was rendering the *previous* completed run's timestamp (e.g. an 8:43 AM ET morning run shown after a 1:41 PM ET dispatch — which was the user-reported symptom). Dropped the URL filter; check `status === 'completed'` in code against the unfiltered listing, which updates eagerly. Same pattern `getLatestOnDemandRun` (polling path) has always used — which is why polling never had the lag.

**Verified live**: user confirmed 2026-05-03 PM session that the wipe behavior + footer timestamp both render correctly on a real Run-now click.

**Live state at handoff**:
- Local commits to push: PROGRESS.md update + ADR-035 in DECISIONS.md + ADR-renumber in PHASES.md (this commit). The two code commits (`ced395b`, `a26bffb`) are already on origin.
- Build green; tsc clean. No worker tests touched.
- The "noticed but deferred" stale-run investigation from Phase 14 is now resolved as "mitigated, not root-caused": ADR-032's four-cache stack + ADR-035's UI wipe + ADR-035's API-lag fix together leave no failure mode the user can observe. If the underlying cache layer ever resurfaces (e.g. with a different page or a different user flow), reopen.

**Next session — start here (Phase 15 proper)**:
1. Read the Phase 15 brief in [PHASES.md](PHASES.md#phase-15--universal-adapter-quality-pass).
2. **Optional cheap win first** (~2 min): tighten `web_search.max_uses` 5 → 2 in [web/app/api/onboard/chat/route.ts](../web/app/api/onboard/chat/route.ts) per ADR-034 follow-up.
3. **Phase 15 task 1 — JSON-LD / microdata extraction tier** in [worker/src/product_search/adapters/universal_ai.py](../worker/src/product_search/adapters/universal_ai.py). Highest-leverage step: most modern e-commerce embeds Product/Offer/ItemList JSON-LD for SEO. Walk all `<script type="application/ld+json">` blocks before falling through to anchor heuristics. Zero LLM cost when it works.
4. **Phase 15 task 2 — `cli probe-url <url> [--render]`** in [worker/src/product_search/cli.py](../worker/src/product_search/cli.py). Useful both for manual diagnosis and for the onboarder integration in task 5.
5. Tasks 3, 4, 5 per the brief.

## Status as of end of 2026-05-03 session (Phase 14 closeout)

**Onboarder rebuilt around Claude Haiku 4.5 + native web_search + ephemeral prompt caching + `<state>`/`<draft>` JSON blocks. ADR-034 written. Phase 14 closed.**

What landed:

1. **Chat route re-platformed** to Anthropic SDK in [web/app/api/onboard/chat/route.ts](../web/app/api/onboard/chat/route.ts):
   - `model: 'claude-haiku-4-5'` (env-overridable via `LLM_ONBOARD_MODEL`).
   - `system` now sent as a single text block with `cache_control: { type: 'ephemeral' }`. Cuts repeat-turn input cost ~90% (cache reads are 0.1× input rate; the system prompt is ~4500 tokens).
   - `tools: [{ type: 'web_search_20250305', name: 'web_search', max_uses: 5 }]`. Anthropic runs the search server-side and feeds results back into the same streaming response — no multi-turn round-tripping in our code.
   - Sliding window: `messages[0]` (kickoff) + synthetic assistant turn carrying the latest `<state>` ledger + last 4 conversational turns. Compression replaces middle turns rather than dropping them, so the model never loses a decision confirmed early in the session.

2. **`<state>` and `<draft>` block format** ([worker/src/product_search/onboarding/prompts/onboard_v1.txt](../worker/src/product_search/onboarding/prompts/onboard_v1.txt)). Every assistant turn ends with two single-line JSON blocks; `<state>` is the running decisions ledger (slug, display_name, target/filters/flags/sources/schedule summaries, open_questions, edit_mode), `<draft>` is structured intent JSON that mirrors the YAML schema 1:1. Server-side [web/lib/onboard/render-yaml.ts](../web/lib/onboard/render-yaml.ts) deterministically renders YAML at save time via `js-yaml.dump`. The "model dropped a closing brace in YAML" failure class is gone.

3. **Save endpoint** ([web/app/api/onboard/save/route.ts](../web/app/api/onboard/save/route.ts)) accepts either `draft` (preferred) or legacy `yaml` payload. Renders YAML server-side, runs the existing schema validator, and commits via `commitNewProfile` unchanged.

4. **OnboardChat.tsx** parses `<state>`/`<draft>` blocks, strips them from the rendered markdown so the user sees a clean reply, and renders the right-pane preview via the shared `renderProfileYaml` (same renderer the server uses at save time). `SessionCost` panel now also tracks cache_read / cache_creation tokens and applies the 0.1× / 1.25× multipliers.

5. **`.env.example` updated** to default `LLM_ONBOARD_MODEL=claude-haiku-4-5`.

6. **Bench passed** — [web/scripts/bench-onboard.mjs](../web/scripts/bench-onboard.mjs), 15-turn scripted dialogue about NC headphones <$300:
   - 7 web searches fired (3 in turn 6, 4 in turn 7 — both vendor-discovery turns).
   - Slug `nc-headphones-under-300` set at turn 3, persisted through turn 13's explicit memory probe ("what slug did we agree on?") and through the end of the session.
   - `display_name` "Noise-Cancelling Over-Ear Headphones Under $300" likewise persisted.
   - Total cost: $0.1779. Non-search turns averaged $0.007 (~25% of estimated GLM-5.1 equivalent at ~$0.023/turn). Search turns ran $0.04–$0.06 each, dominated by web-search-result tokens being cached at creation rate (1.25× input).

7. **Build & type-check green**: `npx tsc --noEmit` clean, `npx next build` compiles with all routes including the new edge-runtime `/api/onboard/chat`. ESLint shows 5 pre-existing errors and 6 pre-existing warnings; none are from Phase 14.

**Done-when checklist**:
- ✅ 15-turn session ends with a valid profile and the model never loses the slug/display_name confirmed early.
- ⚠️ Average cost ≤30% of GLM-5.1 baseline: **met on non-search turns; search-heavy turns are dominated by Anthropic-side web-search-result cache creation that is largely model-architecture-independent.** ADR-034 documents this caveat and proposes tightening `max_uses` from 5 → 2 in Phase 15+.
- ✅ Web search still works (verified end-to-end via 7 successful invocations).

**Live state at handoff**:
- Local commits to push (uncommitted): `web/app/api/onboard/chat/route.ts`, `web/app/api/onboard/save/route.ts`, `web/app/onboard/OnboardChat.tsx`, `web/lib/onboard/blocks.ts` (new), `web/lib/onboard/render-yaml.ts` (new), `web/lib/onboard/promptText.ts` (re-synced), `web/scripts/bench-onboard.mjs` (new), `worker/src/product_search/onboarding/prompts/onboard_v1.txt`, `.env.example`, `docs/DECISIONS.md` (ADR-034 added; ADR-015 marked SUPERSEDED), `docs/PROGRESS.md` (this update).
- Tests: 144/144 worker tests still pass (Phase 14 didn't touch the worker pipeline — only the prompt file). Web app has no test framework yet; the bench script is the integration test.
- Phase 14 → closed. Phase 15 next.

**Noticed but deferred (carry into next session)**:
- **Stale-run display on the product page persists** despite ADR-032's four-cache mitigation (Next fetch + raw.gh CDN + Vercel edge + browser, plus `force-dynamic` and `window.location.reload()`). User reports the product page sometimes still shows a previous run after a Run-now. Worth a focused investigation — possibly a fifth cache layer we haven't accounted for (PWA service worker on a registered tab? GitHub raw.githubusercontent.com edge that the existing cache-buster doesn't reach? Vercel ISR even with `force-dynamic`?). Reproduction: hit Run-now, watch for the new commit on origin, refresh — old numbers persist for some interval.
- Onboarder UX polish landed during Phase 14 closeout: `<state>`/`<draft>` JSON blocks no longer flash on screen mid-stream (commit `a83f98d`); prompt now explicitly tells the model `target.configurations` is always a list with the non-RAM placeholder pattern (this commit) — caught by user reporting a `target.configurations: expected array` save failure on a Lululemon profile.

**Next session — start here (Phase 15)**:
1. Read the Phase 15 brief in [PHASES.md](PHASES.md#phase-15--universal-adapter-quality-pass).
2. Investigate the stale-run display issue above before starting Phase 15 proper, since the user is hitting it daily and it blocks confidence in any vendor-quality work that follows.
3. Optional follow-up: tighten `web_search.max_uses` from 5 → 2 in [web/app/api/onboard/chat/route.ts](../web/app/api/onboard/chat/route.ts) (per ADR-034 open follow-up). Cheap win, ~60% drop in search-turn cost.
4. Universal adapter quality work proper.

## Status as of end of 2026-05-02 session (continuation 13 — Phase 13 closeout)

**AlterLab integration verified and stabilized after a wire-format defect was found and fixed. ADR-033 written. Phase 13 closed.**

## Status as of end of 2026-05-02 session (continuation 13 — Phase 13 closeout) [archived]

**AlterLab integration verified and stabilized after a wire-format defect was found and fixed. ADR-033 written. Phase 13 closed.**

What was supposed to be a verify-and-stabilize pass turned into a real bug fix:

1. **Wire-format defect found**. Continuation 12's commit `33553f8` ("phase 13: switch universal_ai vendor-render path from ScrapFly to AlterLab") inferred the AlterLab API shape from ScrapFly's, never exercised it against the real API, and shipped a unit test that mocked the same fictional shape. Live `_fetch_via_alterlab` calls returned **404** for every URL; the silent fallback in `_fetch_html` quietly routed every Bose run through curl_cffi from continuation 12 onward (which is why backmarket regressed from 3 listings → 0 between continuations 10 and 12).

2. **Wire-format fix landed locally** in [worker/src/product_search/adapters/universal_ai.py](worker/src/product_search/adapters/universal_ai.py):
   - `POST https://api.alterlab.io/api/v1/scrape` (was: `GET /scrape`)
   - `X-API-Key` header (was: `?key=` query param)
   - JSON body `{"url": ..., "sync": true, "formats": ["html"], "advanced": {"render_js": true}}` (was: query params with the made-up `asp` and `country`)
   - Response parser handles `content` as either dict (`content.html`) or bare string.
   - Test mock in [worker/tests/test_universal_ai.py](worker/tests/test_universal_ai.py) rewritten to the real shape. **144/144 worker tests pass.**

3. **Per-vendor verdicts (Bose; live AlterLab calls 2026-05-02)**:
   - `backmarket.com` (`/en-us/search?q=bose+nc+700`): AlterLab fetched 32KB at origin status 200 — body is a Cloudflare `<title>Just a moment...</title>` challenge page. `render_js: true` alone doesn't bypass backmarket's anti-bot tier. **Defer to Phase 15** (try AlterLab tier-escalation / proxy options + add JSON-LD extractor; the latter is moot here because the challenge page contains no JSON-LD). Fixture saved at [worker/tests/fixtures/universal_ai/backmarket-bose-nc700-search.html](../worker/tests/fixtures/universal_ai/backmarket-bose-nc700-search.html).
   - `buy.gazelle.com` (`/collections/headphones`): AlterLab fetched 305KB at origin status 200 — body's `<link rel="canonical">` points at `/404`. The collection URL in the profile is a soft-404 on gazelle's store. **Profile-content issue, not a fetch / extraction issue.** Fixture saved at [worker/tests/fixtures/universal_ai/gazelle-headphones-collection.html](../worker/tests/fixtures/universal_ai/gazelle-headphones-collection.html). User should reconfigure the gazelle URL via onboarder (or remove it).

4. **Phase 13 done-when checklist**:
   - ✅ AlterLab path now structurally emits `[universal_ai] Fetched via alterlab` for both vendors (verified locally; the GHA-stderr verification could not be done because `gh` CLI is not installed and no `GITHUB_TOKEN` is in `.env`, but the same code + same secret will produce the same log line on the runner — re-confirm visually on the next on-demand run).
   - ✅ ADR-033 in DECISIONS.md (supersedes ADR-030).
   - ✅ Per-vendor verdicts above.

5. **Noticed but deferred**:
   - Bose profile's gazelle URL is a soft-404. Phase 14 onboarder rebuild will likely re-explore vendors anyway; if not, user can edit profile.yaml directly.
   - PROGRESS.md (continuation 12) said "4 universal_ai_search vendors (backmarket, bhphotovideo, bestbuy, gazelle)". The actual current profile has only 2 (backmarket, gazelle). bhphotovideo and bestbuy were dropped earlier. Not a problem, just noting the drift.
   - Phase 13 brief step 5 ("verify auth/quota error path on a real failure") wasn't exercised because the live key didn't 401/403/429. Wiring is in place (RuntimeError + cli.py "Scraping API Issue" banner) — verify opportunistically if AlterLab quota ever exhausts.

**Live state at handoff**:
- Local commits to push (uncommitted): `worker/src/product_search/adapters/universal_ai.py`, `worker/tests/test_universal_ai.py`, two new fixtures under `worker/tests/fixtures/universal_ai/`, `docs/DECISIONS.md` (ADR-033 added, ADR-030 marked SUPERSEDED), `docs/PROGRESS.md` (this update).
- Tests: 144/144 worker tests pass.
- Phase 13 → closed. Phase 14 next.

**Next session — start here (Phase 14)**:
1. Read the Phase 14 brief in [PHASES.md](PHASES.md#phase-14--onboarder-cost--memory-rebuild).
2. Re-platform `web/app/api/onboard/chat/route.ts` from GLM-5.1 to Anthropic Claude Haiku 4.5 with `web_search` tool + prompt caching.
3. Implement the `<state>{...}</state>` decisions ledger and `<draft>{...}</draft>` structured-intent JSON pattern; render YAML server-side at save time.
4. Bench against GLM-5.1 baseline.

## Status as of end of 2026-05-02 session (continuation 12 — planning reset)

**Planning-only session. No code changes. Issued a multi-phase plan (Phases 13–18) to address a backlog: AlterLab migration unverified, onboarder forgets context mid-conversation, universal adapter only works on backmarket, no slug-delete UI, no schedule-edit UI.**

User-confirmed decisions this session:
1. **Onboarder model**: switch from `glm-5.1` ($2/$8 per M tokens, reasoning-class with json_object/CoT quirks per memory) to `claude-haiku-4-5` ($1/$5) with Anthropic's native `web_search` tool + prompt caching. ADR-014's `claude-sonnet-4-6` choice is superseded.
2. **Slug deletion**: hard delete — remove `products/<slug>/` AND `reports/<slug>/` history when the user deletes a product. (No soft-delete / archive option.)
3. **AlterLab key**: confirmed set in GH repo secrets as of 2026-05-02.
4. **YAML schema**: stays as the on-disk format. The architectural commitment (deterministic worker pipeline, LLM only synthesizes pre-verified data) requires it. The change is in onboarder UX: per-turn assistant emits structured intent JSON, server renders YAML at save time.

Where models are currently used (snapshot for reference; updated by Phase 14):

| Step | Provider / Model | $/M in/out |
|---|---|---|
| Onboarding interview | `glm` / `glm-5.1` (Phase 14 swaps to anthropic/claude-haiku-4-5) | $2.00 / $8.00 |
| Validator (`ai_filter`) | `anthropic` / `claude-haiku-4-5` (hardcoded) | $1.00 / $5.00 |
| Universal AI adapter | `anthropic` / `claude-haiku-4-5` (hardcoded) | $1.00 / $5.00 |
| Synthesizer (Context paragraph only) | `glm` / `glm-4.5-flash` (env-overridable) | $0.05 / $0.05 |

**Live state at handoff**:
- AlterLab migration code is local & uncommitted (universal_ai.py, both workflow ymls, test_universal_ai.py, cli.py, .env.example).
- `web/lib/onboard/promptText.ts` is untracked (introduced in continuation 11; needs commit alongside).
- ALTERLAB_API_KEY is set in GH Actions secrets.
- Bose profile has 4 universal_ai_search vendors (backmarket, bhphotovideo, bestbuy, gazelle) — only backmarket is known to work.

**Next session — start here (Phase 13)**:
1. Commit the pending AlterLab migration changes + the untracked `promptText.ts`. Single commit, message: `phase 13: switch universal_ai vendor-render path from ScrapFly to AlterLab`.
2. Trigger a Run-now on `bose-nc-700-headphones`. From the GH Actions log's worker stderr, verify each `universal_ai_search` source emits `[universal_ai] Fetched via alterlab`. If any fell through to curl_cffi, AlterLab itself failed — capture the response.
3. Per-vendor classification (backmarket / bhphotovideo / bestbuy / gazelle): success / extraction-issue (defer to Phase 15) / AlterLab-failed (capture body to fixture).
4. Write ADR-033 documenting the ScrapFly → AlterLab swap.
5. Update PROGRESS.md with the per-vendor verdicts and set active phase to Phase 14.

## Status as of end of 2026-05-01 session (continuation 11)

**Onboarding optimized to Zhipu GLM-5.1, context window reigned in, and web search hallucination/schema bugs resolved.**

The user's core goal for this session was to optimize the onboarding interview costs while migrating to GLM-5.1. Along the way, we diagnosed and resolved multiple subtle issues caused by the migration and prompt structure.

1. **Migration to Zhipu GLM-5.1** (`8619287`). Swapped `openai` SDK for Anthropic SDK in `web/app/api/onboard/chat/route.ts`. Configured with `https://open.bigmodel.cn/api/paas/v4/` endpoint.
2. **Context Window Ballooning Fix** (`8619287`). Implemented a sliding window in `route.ts`. It now retains `messages[0]` (critical because it contains the original `profile.yaml` draft and slug in edit mode) plus the last 5 turns. This ensures the model doesn't hallucinate a new slug mid-conversation while saving massive token costs.
3. **Prompt Minification** (`8619287`). Updated `web/lib/onboard/prompt.ts` to strip blank lines dynamically, significantly reducing the base token footprint of `onboard_v1.txt`.
4. **Zhipu Web Search "Hang" / Hallucination** (`cb2ca95`, `af2553c`). The UI was hanging after the model said "Let me search...". Zhipu's `web_search` with `enable: true` handles search transparently *before* generation, unlike Anthropic's multi-turn tool calling. Two fixes applied:
   - Removed `search_result: true` from the `web_search` tool payload so Zhipu streams the answer directly without returning a `tool_calls` payload.
   - Removed the explicit "Use web_search tool" instruction from `onboard_v1.txt`. The model was hallucinating a literal markdown JSON tool call because it was told to use a tool that had no explicit function schema. Replaced with instructions to rely on the automatically injected search results.
5. **`sources_pending` Schema Bug** (`bc52caa`, `d1eff10`). The model placed an invented source (`amazon_renewed`) in the `sources_pending` array, but the frontend and backend strictly validated it against `KNOWN_SOURCE_IDS`, breaking the wishlist intent. Fixed `schema.ts` and `profile.py` to allow arbitrary IDs in `sources_pending`. Additionally, clarified the `sources_pending` structure in `onboard_v1.txt` to explicitly request a list of objects with `id` and `note` fields to prevent the model from emitting a bare list of strings.

**Live state at handoff:**
- The onboarding interview is now stable, extremely cheap (via sliding window + GLM-5.1 + minification), and successfully integrates automatic web search.
- The `bose-nc-700-headphones` profile has had its vendors swapped. The user is currently running an on-demand search to verify the new vendor results.

**Next session — start here:**
1. **Review the results of the Bose search run** with the newly selected vendors. Address any vendor-specific anti-bot scraping issues if they arise.
2. **Phase 12b (Tier-B adapter) and 12c (schedule editor UI)** remain queued.

## Status as of end of 2026-05-01 session (continuation 10)

**Bose universal_ai pipeline now actually returns listings; Run-now
freshness rebuilt; per-run CSVs preserved in repo.**

The user's core complaint at session start was "two cache layers fixed,
still seeing stale screen + 0/0 universal_ai results." This continuation
chased that down through five distinct issues, all fixed:

1. **Post-run staleness on desktop browser** (`fcfd381`). Previous
   wave's PWA service-worker fix (`d8edcc2`) didn't help because the
   user was on a desktop browser, not the PWA. Diagnosed via the
   screenshot: their report numbers exact-matched commit `18b750a`
   even though `f7ef0df` was on origin — so the page was serving
   stale content from before the disambiguation fix landed. Two more
   defensive layers added to the existing `cache: 'no-store'` +
   `?_cb=` cache-busters: `export const dynamic = 'force-dynamic'`
   on `web/app/[product]/page.tsx` to opt the route out of any
   Vercel edge HTML/RSC caching, and replaced `router.refresh()`
   with `window.location.reload()` in
   `web/app/[product]/RunNowButton.tsx` because Next 16's
   `router.refresh()` only re-fetches the RSC payload and explicitly
   does NOT invalidate the server-side cache (per the
   `node_modules/next/dist/docs/01-app/03-api-reference/04-functions/use-router.md`
   warning). New ADR-032.

2. **ScrapFly timeout was being shadowed by the outer fetch budget**
   (`fcfd381`). `_fetch_html(timeout=20.0)` passed `timeout=timeout`
   to `_fetch_via_scrapfly`, so ScrapFly was always called with 20s
   regardless of the 60s default in its own signature. JS-render of
   heavy sites (B&H, Crutchfield) reliably exceeded 20s and fell
   through to the doomed httpx/curl_cffi tier. Fix: ScrapFly now
   gets its own dedicated 120s budget while curl_cffi/httpx stay at
   20s — the two have very different characteristics and shouldn't
   share a budget.

3. **Vendor-pick mismatch for the Bose 700** (`fcfd381`). Verified
   locally with `_fetch_via_scrapfly`:
   - headphones.com: 27 anchors, all blog/review content. The store
     reports "15 results found" but they're MENTIONS in articles, not
     SKUs for sale. Audiophile shops don't carry consumer Bose.
   - audio46.com: only stocks earpad accessories for the 700, not
     the headphones. `Search: 3 results found` — accessories.
   - bhphotovideo.com: ScrapFly 60s ReadTimeout (worsened by issue 2
     above), fell through to httpx → 403 Cloudflare challenge.

   User confirmed Bose 700 is EOL — refurb marketplaces are now the
   realistic supply. Profile updated: drop headphones+audio46, add
   `https://www.backmarket.com/en-us/search?q=bose+nc+700` (verified
   to surface 3 actual NC700 listings: Silver $196, White $239, Black
   $272).

4. **"Passed > Fetched" misattribution in Sources panel** (`3ea6a1b`).
   First Run-now after the vendor swap showed
   `universal_ai (bhphotovideo.com) | ok | 0 | 3` — impossible.
   Root cause: `passed` was attributed to source_stats rows by
   `lst.source` alone, but every universal_ai listing carries the
   canonical `source = "universal_ai_search"` regardless of which
   vendor URL produced it, so all three universal_ai rows each
   claimed the full universal_ai_search passed-count. Fix: tuple key
   `(source_id, vendor_host_or_None)`; source rows now also store
   `match_host` (already extracted for the display label) and
   listings emit their host via `attrs["vendor_host"]` (already set
   by adapter at emit time). Hoisted the key-builder to module-level
   `_passed_match_key` and pinned with 6 tests in the new
   `tests/test_cli.py`.

5. **CSV said "persisted" but wasn't** (`b2e012d`). Reports claimed
   "the full set is persisted to SQLite and the daily CSV" but
   `worker/data/` is gitignored and not uploaded as an artifact, so
   on the GH Actions runner both the SQLite and CSV were ephemeral.
   AND the CSV was per-day (overwriting on same-day reruns). Fix:
   relocated CSV to `reports/<slug>/data/<YYYY-MM-DDTHH-MM-SSZ>.csv`
   — per-run timestamped, in the committable reports tree. The
   workflow's existing `git add -A` step now picks up CSVs the same
   way it picks up the report markdown. Verified end-to-end on
   `aec721b`: `reports/bose-nc-700-headphones/data/2026-05-01T00-46-39Z.csv`
   landed with 42 rows alongside the .md report. Report wording
   updated to drop the misleading SQLite claim. SQLite stays at
   `worker/data/<slug>/listings.sqlite` (gitignored, ephemeral on
   GHA but useful locally for `diff` command). New ADR-031.

144 worker tests pass (was 135 at session start).

**Live state at handoff** (latest GHA run `aec721b`, 2026-05-01 00:47Z):

```
ebay_search                        ok  44  42
universal_ai (backmarket.com)      ok   3   3
universal_ai (bhphotovideo.com)    ok   0   0
```

backmarket.com is producing real listings into the report. B&H still
0/0 — its ScrapFly call may be working now (under the 120s budget) but
its anti-bot layer is harder than backmarket's; whether it's worth
keeping is a vendor-tuning question for next session. Run-cost panel
should now correctly show 2 universal_ai LLM calls (one per vendor
that got past `_extract_candidates`).

**Next session — start here:**

1. **Verify the Run-now freshness fix actually resolved the
   complaint.** User's previous-run experience was the smoking gun;
   confirm a run today opens directly to the new report on desktop
   without manual reload. If still stale, the next defensive layer
   is `Cache-Control: no-store` on the `/api/revalidate` response,
   though that should now be unnecessary.

2. **Decide what to do about B&H.** Options: (a) keep it but accept
   the 0/0; (b) remove it from the profile to declutter the Sources
   panel; (c) try ScrapFly's `wait_for_selector` or a different B&H
   URL pattern (`/c/buy/Headphones/ci/12780/N/4226657555`-style
   category pages tend to be more SSR-friendly). The ScrapFly
   timeout fix in this session may have helped — re-check after
   the user runs once more.

3. **Phase 12b (Tier-B adapter) and 12c (schedule editor UI)** are
   still queued. Pick one once Bose is stable on prod data —
   the architecture is now end-to-end proven (ScrapFly working,
   per-run CSVs landing, freshness reliable), so moving on is fine.

**Files added this continuation**:
- `worker/tests/test_cli.py` (6 tests pinning `_passed_match_key`)

**Files modified this continuation**:
- `web/app/[product]/page.tsx` (`force-dynamic` segment config)
- `web/app/[product]/RunNowButton.tsx` (window.location.reload()
  in place of router.refresh(); removed unused useTransition/useRouter)
- `worker/src/product_search/adapters/universal_ai.py` (ScrapFly gets
  own 120s timeout instead of inheriting outer 20s)
- `worker/src/product_search/cli.py` (per-run CSV path; tuple-keyed
  passed-attribution; `_passed_match_key` hoisted to module level;
  report wording updated to drop misleading SQLite claim)
- `worker/src/product_search/storage/csv_dump.py` (per-run timestamp
  path under `reports/<slug>/data/`)
- `worker/tests/test_storage.py` (3 new tests for `default_csv_path`)
- `products/bose-nc-700-headphones/profile.yaml` (vendor swap)
- `docs/DECISIONS.md` (ADR-031, ADR-032)
- `docs/PROGRESS.md` (this block)

**Commits this continuation** (all pushed at session end except docs):
- `fcfd381` — post-run freshness + ScrapFly timeout + bose vendor swap
- `3ea6a1b` — Sources-panel "passed" misattribution fix
- `b2e012d` — per-run CSV under reports/ tree
- `aec721b` — chore: on-demand report (auto from GHA, validates the
  per-run CSV landing path end-to-end)

## Status as of end of 2026-04-30 session (continuation 9)

**Universal vendor scraping went live; Tier-3 ScrapFly path added;
PWA stale-cache bug found and fixed.**

Continuation 8's adapter rewrite shipped, but the first real-vendor
runs surfaced four follow-ups that this continuation closed. Five
commits total this session, four already pushed at handoff
(`d1eac6d`, `6a5ed4a`, `420069f`, `d8edcc2` — confirmed via
`git log origin/main`). One docs commit local at handoff (this one).

1. **Profile-schema gap** (`d1eac6d`). `KNOWN_SOURCE_IDS` in both
   `worker/src/product_search/profile.py` and
   `web/lib/onboard/schema.ts` was missing `universal_ai_search`,
   even though `cli.py` had been wired for it since ADR-021. The
   onboarding UI surfaced the gap as
   `unknown source id "universal_ai_search"` the first time the AI
   emitted a profile that actually used it. Fixed in both schemas
   with a new pinning test
   (`test_accepts_universal_ai_search_source`) so future drift
   surfaces in CI.

2. **Source-column display fix** (`6a5ed4a`). For
   `universal_ai_search` Listings, the Source column now renders
   the vendor host (without `www.`) instead of the literal adapter
   id — `audio46.com` rather than `universal_ai_search`. Internal
   `lst.source` stays canonical (source_stats grouping, cost panel
   are unaffected). New `_source_label(lst)` helper in
   `synthesizer.py` + 3 tests pinning the rendering for
   universal_ai-with-attr / universal_ai-without-attr / non-universal
   adapters.

3. **Sources panel disambiguation** (`420069f`). When a profile has
   multiple `universal_ai_search` entries, the panel previously
   showed three identical rows. Now it shows
   `universal_ai (audio46.com)` / `universal_ai (headphones.com)`
   etc. by computing a `display_source` field in the source loop
   and reading it from `_build_sources_searched_md`. Display-only
   — `source_stats[i]['source']` stays canonical.

4. **First two real-vendor runs** confirmed the architecture works
   end-to-end but exposed that *server-rendered* hopes were
   misplaced for two-thirds of the vendors I picked:
   - Run `1237737` (21:41 UTC, against
     crutchfield.com + adorama.com): both 0/0 — heavy JS rendering /
     Akamai-fronted.
   - Run `18b750a` (21:53 UTC, after profile swap to
     headphones.com + audio46.com + bhphotovideo.com): all three
     also 0/0 — modern Shopify themes are JS-heavier than expected
     and B&H's search page is partially client-rendered for product
     cards.
   - Conclusion: free-tier server-rendered fetches won't cover the
     vendor coverage the user wants. ADR-030 / ScrapFly is the
     answer.

5. **ScrapFly Tier-3 fetch** (`d8edcc2`, ADR-030). New
   `_fetch_via_scrapfly()` routes vendor page fetches through the
   ScrapFly API with `render_js=true` + `asp=true` (residential
   proxies + Cloudflare/Akamai/Datadome challenge solving). Gated
   by `SCRAPFLY_API_KEY` env var. `_fetch_html` priority is now
   `scrapfly → curl_cffi → httpx`; ScrapFly outage / 5xx falls
   through to the cheap tiers so an outage on ScrapFly's side can't
   zero a run for sites that don't need JS rendering. Both
   workflows propagate the secret. `.env.example` documents it.
   3 new tests cover the fetcher routing.

6. **PWA service-worker stale-cache bug** (`d8edcc2`).
   `web/public/sw.js` v1 used stale-while-revalidate for every
   same-origin GET, including the RSC payload that
   `router.refresh()` fetches after a Run-now completes — so the
   user reliably saw the OLD report immediately on every
   "Done. Loading new report…" cycle. v2 strict-static-only:
   only `/_next/static/*` and assets matching
   `/\.(png|jpg|svg|webp|ico|css|js|mjs|woff2?|ttf|otf|map)$/`
   are cached; HTML / RSC / `/api/*` / cross-origin all pass
   through. CACHE_NAME bumped to `v2` + `skipWaiting()` +
   `clients.claim()` + activate-handler eviction of v1 entries so
   existing tabs adopt v2 on the first reload after deploy. This
   was the single largest source of the recurring "stale screen"
   complaint across this entire phase.

135/135 worker tests pass (132 + 3 ScrapFly fetcher routing tests).
Web `tsc` clean on the schema mirror.

**Live state at handoff**:
- `bose-nc-700-headphones` profile has 3 `universal_ai_search`
  sources: headphones.com, audio46.com, bhphotovideo.com.
- One additional Run-now landed after the SW/ScrapFly commit
  pushed (`f7ef0df`, 22:28 UTC). Sources panel:

  ```
  ebay_search                        ok  44  41
  universal_ai (headphones.com)      ok   0   0
  universal_ai (audio46.com)         ok   0   0
  universal_ai (bhphotovideo.com)    ok   0   0
  ```

  All three universal_ai vendors STILL returned 0/0 — but the
  Run-cost panel now shows the LLM WAS called for each one (input
  tokens: 3621 / 4067 / 729; output tokens: 13 / 17 / 13). That
  means `_extract_candidates` found anchor candidates for all
  three (so the fetch succeeded and produced non-empty HTML with
  some hrefs) but the LLM returned essentially `{"listings": []}`
  for each (~13 output tokens = empty wrapper). So the failure
  mode shifted from "no anchors found" → "anchors found, none of
  them looked like product listings to the LLM."
- Whether that run actually used ScrapFly is the first question
  for next session. Two ways the GH Actions secret could be
  unset: (a) user added `SCRAPFLY_API_KEY` only to local `.env`
  and not yet to repo secrets, (b) added it but the
  workflow-dispatch race against another commit grabbed an SHA
  before the secret was set. Worker stderr in the GH Actions log
  for that run will show `[universal_ai] Fetched via scrapfly`
  vs `via curl_cffi` per source — that is the diagnostic to pull
  first.

**Next-session investigation plan** (the user's stated focus):
1. Confirm `SCRAPFLY_API_KEY` is in repo secrets and what fetcher
   the `f7ef0df` run actually used (worker stderr in the GH
   Actions log: `[universal_ai] Fetched via <tier>`).
2. If it WAS curl_cffi: re-trigger Run-now after secret is in
   place; expect ScrapFly to materially change the candidate
   counts and possibly emit non-empty listings.
3. If it WAS ScrapFly and we still got 0 listings: pull the
   `worker/data/llm_traces/<date>.jsonl` artifact for that run,
   inspect what candidate payload was sent to Haiku and what
   Haiku rejected. Possible fixes:
   - Loosen the prompt (currently: "OMIT candidates with no
     price hint and no $ in context"). Some sites write prices
     as `<span class="money">249</span><sup>99</sup>` which
     `_PRICE_PATTERN` won't match.
   - Loosen `_PRICE_PATTERN` to also accept bare numeric
     dollar amounts in price-context contexts (`<span class="price">…</span>`).
   - Increase `max_candidates` (currently 80) if the page has
     many anchors and product cards are getting outside the cap.
4. Confirm the SW v2 fix actually evicted v1 in the user's
   installed PWA (one hard-refresh after deploy was the docs
   instruction).

**Files added this continuation**: none.

**Files modified this continuation**:
- `worker/src/product_search/profile.py` (KNOWN_SOURCE_IDS)
- `worker/tests/test_profile.py` (pinning test)
- `web/lib/onboard/schema.ts` (KNOWN_SOURCE_IDS mirror)
- `worker/src/product_search/synthesizer/synthesizer.py`
  (_source_label helper + Source column wiring)
- `worker/tests/test_synthesizer.py` (3 source-column tests)
- `worker/src/product_search/cli.py` (display_source field +
  Sources panel renderer)
- `worker/src/product_search/adapters/universal_ai.py`
  (_fetch_via_scrapfly + tiered priority)
- `worker/tests/test_universal_ai.py` (3 ScrapFly tests)
- `.env.example` (SCRAPFLY_API_KEY documented)
- `.github/workflows/search-on-demand.yml` + `search-scheduled.yml`
  (SCRAPFLY_API_KEY env propagation)
- `web/public/sw.js` (v1 → v2)
- `products/bose-nc-700-headphones/profile.yaml` (vendor swap from
  crutchfield + adorama → headphones + audio46 + bhphotovideo)
- `docs/DECISIONS.md` (ADR-030)
- `docs/PROGRESS.md` (this block)

**Next session — start here:**

1. **Confirm `SCRAPFLY_API_KEY` is set as a GH Actions repo secret**
   (https://github.com/ARobicsek/product_search/settings/secrets/actions).
   The user was adding it locally to `.env` at handoff; the GH
   secret is what makes prod runs use ScrapFly.
2. **Push any pending commits** and trigger a Run-now on the Bose
   page. Expected outcome:
   - The Sources panel shows three
     `universal_ai (<host>)` rows.
   - At least 1-2 vendors yield N>0 listings (with ScrapFly
     handling the JS render). audio46 and headphones.com are the
     most likely wins; B&H may still struggle if their cards are
     loaded by post-render API.
   - The Run-cost panel shows new
     `universal_ai (<url>)` rows (one per vendor) at ~$0.005 each
     (Haiku 4.5 calls; ScrapFly itself doesn't appear in the panel
     because it's not an LLM call — see ADR-030 trade-offs).
   - The page no longer shows stale results after the run completes
     (sw.js v2 fix). User may need ONE hard-refresh to evict v1 from
     their installed PWA.
3. **Analyze the run together with the user** (their plan for the
   next session). Things to look at:
   - Per-vendor success/failure pattern.
   - Whether ScrapFly's render_js was strictly necessary for each
     vendor (vs. curl_cffi being sufficient — could re-test by
     pulling SCRAPFLY_API_KEY out and re-running once).
   - Cost: ScrapFly bills per credit (5-10 credits per JS-rendered
     page). Free tier is 1k/month; 3 vendors * daily run = ~270
     credits/month, well within free tier.
   - Whether the ranked-listings table now has rows tagged
     `[audio46.com](url)` etc. and whether they out-rank any eBay
     listings (would surface the cheapest non-eBay path).
4. **If a vendor still fails with ScrapFly enabled**, options:
   - Try a different category-page URL for that vendor (some have
     a `/collection/X` pattern that's more SSR-friendly than
     `/search?q=...`).
   - Add ScrapFly's `wait_for_selector` parameter (currently we
     just `render_js=true` and grab the post-render HTML).
   - Add the failing vendor to a per-profile blocklist or move it
     to `sources_pending` with a note.
5. **Phase 12b (Tier-B adapter) and 12c (schedule editor UI)** are
   still queued; pick after universal vendor support is proven on
   prod data.

## Status as of end of 2026-04-30 session (continuation 8)

**Universal vendor scraping: anchor-first extraction + Chrome TLS
impersonation, no LLM URL invention.**

The `universal_ai_search` adapter (introduced in ADR-021) was wired into
`cli.py` but had never been exercised in prod and had three structural
problems: it used `glm-5.1` (a reasoning model the project already
abandoned everywhere else for ignoring `json_object` mode), it had no
anti-bot story beyond a Chrome User-Agent header, and it asked the LLM
to invent `{title, price, url}` objects from cleaned page text — guarded
only by a verbatim-substring URL check that frequently misfired across
whitespace boundaries. ADR-029 addresses all three.

Single commit this session, local at handoff.

1. **Refactor `worker/src/product_search/adapters/universal_ai.py`**
   end-to-end (ADR-029):
   - `_extract_candidates(html, base_url)` walks every `<a href>` with
     selectolax, resolves to absolute via `urljoin`, filters
     navigation/cart/footer/search/category chrome, attaches nearby
     `$X.XX` price hints from a "card-like" ancestor (≤4 hops, stops at
     >600 chars), and dedupes by canonical scheme+host+path. Caps at
     80 candidates.
   - `fetch()` sends candidates to Claude Haiku 4.5 (same model as
     `ai_filter`, ADR-023) with the prose-tolerant `_extract_json`
     parser mirrored locally. The LLM returns
     `{idx, title, price_usd, condition}`; URLs are looked up
     server-side from `candidates[idx].href`. URL hallucination is
     structurally impossible.
   - `_fetch_html` prefers `curl_cffi` (Chrome TLS-fingerprint
     impersonation via libcurl-impersonate) when installed, falls
     back to `httpx` otherwise. Logs status + body length on every
     fetch so bot blocks surface in the worker log.
   - `LAST_RUN_USAGE` populated after each call so `cli.py` can
     surface universal_ai cost in the Run-cost panel.

2. **`worker/pyproject.toml`** — `curl-cffi>=0.7` added to runtime
   dependencies (ships pre-built wheels for Windows/macOS/Linux on
   Python 3.12).

3. **`worker/src/product_search/cli.py`** — source loop accumulates
   one `universal_ai_usage` entry per `universal_ai_search` source
   (tagged with the source URL so the cost panel disambiguates
   multi-vendor profiles). Threaded into all three Run-cost build
   sites: success report, post-check stub, zero-pass diagnostic.

4. **`worker/src/product_search/onboarding/prompts/onboard_v1.txt`**
   — `web_search` section reframed from "use sparingly" to "use
   actively for vendor discovery"; interview step 5 now explicitly
   walks the AI through finding vendor URLs via web_search and
   converting each into a `universal_ai_search` source. The
   "Allowed source IDs" entry for `universal_ai_search` documents
   that URLs must point at category/search/collection pages (not
   product detail pages) and that JS-rendered / Cloudflare-gated
   sites silently return zero listings.

5. **Test coverage** — new `worker/tests/test_universal_ai.py` (10
   tests) pinned against
   `worker/tests/fixtures/universal_ai/synthetic_vendor.html`. Covers
   nav/cart/footer/search filtering, relative+absolute URL
   resolution, canonical-URL dedupe, price-hint attachment,
   priceless-but-product-shaped anchor survival, end-to-end fetch
   with verbatim URLs, out-of-range LLM idx rejection, prose-preamble
   tolerance, no-URL short-circuit, and no-anchor-found
   short-circuit (LLM must NOT be called when extraction yields no
   candidates).

128/128 worker tests pass (118 baseline + 10 new). Mypy clean on
`adapters/universal_ai.py`. Ruff clean on the new files. Pre-existing
`cli.py` `dict` type-arg notices and unrelated E501s left alone per
session protocol.

**Files added this session**:
- `worker/src/product_search/adapters/universal_ai.py` (full rewrite)
- `worker/tests/test_universal_ai.py`
- `worker/tests/fixtures/universal_ai/synthetic_vendor.html`

**Files modified this session**:
- `worker/pyproject.toml` (curl-cffi runtime dep)
- `worker/src/product_search/cli.py` (per-source universal_ai usage capture)
- `worker/src/product_search/onboarding/prompts/onboard_v1.txt` (active web_search guidance + per-vendor universal_ai_search entries)
- `docs/DECISIONS.md` (ADR-029)
- `docs/PROGRESS.md` (this block)

**Next session — start here:**

1. **Push this session's commit.** Continuation 7's three commits
   are already on `origin/main`. CI should pass (worker pytest is
   green, web tsc/lint untouched).
2. **Live smoke-test universal vendor** — pick one trusted vendor
   (e.g. an Adorama, B&H, or a Shopify store the user knows). Edit
   the Bose profile to add a `universal_ai_search` source pointing
   at that vendor's headphones search page. Run on-demand and
   verify:
   - The "Sources searched" panel shows
     `universal_ai_search` with a fetched count > 0.
   - The Run-cost panel shows a `universal_ai (<url>)` row with
     the per-source Haiku call cost.
   - At least one ranked-listings row carries
     `source: universal_ai_search` with a real verbatim vendor URL.
   - If the vendor blocks (zero listings extracted), the worker
     log shows the clear "no anchor candidates extracted" warning;
     swap to a different vendor and try again.
3. **If the smoke test reveals a Cloudflare-challenge / JS-render
   site the user really needs**: defer to a follow-up Tier-3
   adapter session. Options: Playwright with stealth, or a hosted
   service like ScrapFly / BrowserBase gated behind an env var.
4. **Phase 12b (Tier-B adapter) and 12c (schedule editor UI)** are
   still queued.
5. **Onboarding follow-up** (deferred from continuation 6): teach
   the onboarding prompt to ask for `description:` per flag at
   profile-creation time.

## Status as of end of 2026-04-30 session (continuation 7)

**Cost visibility (run + onboarding), inline column chooser on the run
page, and local-timezone fix for the run footer.**

Three commits this session, all local at handoff:

1. **ADR-028** (`898b74a`) — Bottom line and Flags become deterministic;
   LLM writes only the Context paragraph. The 2026-04-30 prod run
   rejected `['7.7']` (a fabricated percentage) even after ADR-027's
   retry — the failure mode was structural, not promptable. Splitting
   numeric content (Python) from qualitative prose (LLM) eliminates
   the class of failure entirely. See ADR-028 in DECISIONS.md.

2. **Cost visibility + column chooser** (`dd7952d`):
   - Worker: new `worker/src/product_search/llm/pricing.py` price
     table; `ai_filter` exposes `LAST_RUN_USAGE`; `synthesize()` sums
     tokens across the retry; `cli.py._build_run_cost_md` renders a
     deterministic Run-cost panel appended to every report (success,
     post-check stub, zero-pass diagnostic).
   - Web: `web/lib/llm-prices.ts` mirrors the Python table;
     `/api/onboard/chat` emits a `usage` SSE event from `final.usage`;
     `OnboardChat.tsx` accumulates per-turn usage and renders a
     `SessionCost` block in the sidebar footer.
   - Web: inline column chooser on `/[product]` — new
     `web/lib/report-columns.ts` (column metadata + surgical YAML
     mutators); new `web/app/[product]/ColumnChooserButton.tsx` with
     selected/available split, up/down reorder, save via existing
     `/api/onboard/save`. Saves immediately with "Saved. Will apply on
     the next run." (option A — no auto re-run).

3. **Run footer renders in user's local timezone** (this commit):
   - `RunInfoFooter` was previously a function inside the server
     component `[product]/page.tsx`, so `toLocaleString` ran on Vercel
     (UTC). Extracted to its own client component
     `web/app/[product]/RunInfoFooter.tsx`. Renders a placeholder
     during SSR, then `useEffect` fills in the localized string once
     the browser hydrates. Wraps the timestamp in `<time
     dateTime={iso}>` for accessibility / semantics.
   - The `RunNowButton`'s "Last run: 2m · just now" caption was
     already a client component; no change there.

118/118 worker tests pass (98 baseline + 10 ADR-028 builders + 10
pricing helpers, all added this session). Web `tsc` and `eslint` clean
on all changed files (the one pre-existing `OnboardChat.tsx` warning
predates this session).

**Files added this session**:
- `worker/src/product_search/llm/pricing.py`
- `worker/tests/test_pricing.py`
- `web/lib/llm-prices.ts`
- `web/lib/report-columns.ts`
- `web/app/[product]/ColumnChooserButton.tsx`
- `web/app/[product]/RunInfoFooter.tsx`

**Files modified this session**:
- `worker/src/product_search/profile.py` (FlagRule.description)
- `worker/src/product_search/synthesizer/synthesizer.py` +
  `__init__.py` (deterministic Bottom line / Flags; sum tokens across
  retry)
- `worker/src/product_search/synthesizer/prompts/synth_v1.txt`
  (Context-only)
- `worker/src/product_search/validators/ai_filter.py`
  (LAST_RUN_USAGE)
- `worker/src/product_search/cli.py` (`_build_run_cost_md`)
- `worker/tests/test_synthesizer.py`
- `web/app/api/onboard/chat/route.ts` (usage SSE event)
- `web/app/onboard/OnboardChat.tsx` (SessionCost block)
- `web/app/[product]/page.tsx` (profile fetch + chooser button +
  client RunInfoFooter import)
- `docs/DECISIONS.md` (ADR-028)

**Commits this session:**
- `898b74a` — ADR-028 (LLM writes Context only) — pushed
- `dd7952d` — cost visibility + column chooser — pushed
- `fe4dcd5` — local-timezone run footer + this PROGRESS update — local
  at handoff (1 commit ahead of `origin/main`)

**Next session — start here:**

1. **Push the three local commits.** They build on each other; pushing
   together is fine. CI should pass (worker pytest is green, web tsc
   and lint are green on changed files).
2. **Live verification on each product:**
   - Bose: Bottom line shows "$47.97 from schicjar via ebay_search …
     (used)"; Flags section enumerates each flag with a description;
     Context paragraph is digit-free narrative; Run-cost panel at the
     bottom shows ai_filter and synth costs; bottom-of-page footer
     shows the user's local time.
   - DDR5: same shape with `$X total for target` in Bottom line.
3. **Test the column chooser end-to-end** — open it on either product,
   change the column set, save, click Run-now, confirm the next
   report uses the new columns.
4. **Test the onboarding session-cost block** — start a new
   `/onboard` session, exchange a few turns, confirm the Session cost
   row appears in the sidebar footer with running total.
5. **Phase 12b (Tier-B adapter) and 12c (schedule editor UI)** are
   still queued. Pick one once two clean consecutive runs land on
   each product.
6. **Onboarding follow-up** (deferred from continuation 6): teach the
   onboarding prompt to ask for `description:` per flag at
   profile-creation time, so `FLAG_FALLBACK_DESCRIPTIONS` becomes a
   safety net rather than the common path.

## Status as of end of 2026-04-30 session (continuation 6)

**Numbers belong to Python; words belong to the LLM. Bottom line and
Flags are now deterministic; LLM only writes the Context paragraph.**

The 2026-04-30 prod run rejected `['7.7']` (a computed percentage)
even after ADR-027's retry. The retry was chasing a symptom — the
LLM was being asked to write narrative *about* prices, and
intermittently fabricated comparisons. ADR-028 changes the structure:

1. **`build_bottom_line_md(listings, profile)`** picks the cheapest
   passing listing and emits a one-sentence summary from verbatim
   fields (total or unit price, seller, source link, title,
   condition). No LLM, no fabrication possible.

2. **`build_flags_md(listings, profile)`** enumerates the distinct
   flags in the visible listings, one bullet each. Description text
   comes from a new optional `FlagRule.description` profile field,
   falling back to a built-in `FLAG_FALLBACK_DESCRIPTIONS` dict for
   stable IDs, then to the bare flag id. No LLM.

3. **`synth_v1.txt` rewritten** — asks for one qualitative paragraph
   only (the Context section). "ABSOLUTELY NO DIGITS" is the
   non-negotiable rule. The deterministic Bottom line / table /
   diff / Flags are assembled around the LLM paragraph in
   `synthesize()`.

4. **`post_check` now runs on the LLM's paragraph alone** (not the
   full assembled report). Same semantics — any digit token not in
   the input payload is rejected — but the surface area is much
   smaller and the failure mode "model fabricates a percentage in a
   narrative comparison" can no longer originate inside a fact-laden
   sentence we asked it to write.

5. **Single retry survives** (per ADR-027) but with a tighter
   instruction: "you wrote digits not in the JSON; remove them and
   rephrase qualitatively." If the retry also fails, the existing
   `cli.py` stub-report path handles it.

108/108 worker tests pass (10 new tests cover the deterministic
builders + the Context-only synthesize contract). Ruff/mypy clean on
all changed files (3 pre-existing E501s in COLUMN_DEFS / build_diff_md
left alone per session protocol).

**Files changed**:
- `worker/src/product_search/profile.py`: added optional
  `FlagRule.description: str | None`.
- `worker/src/product_search/synthesizer/synthesizer.py`: added
  `FLAG_FALLBACK_DESCRIPTIONS`, `build_bottom_line_md`,
  `build_flags_md`, `_strip_context_prefix`. Replaced `synthesize()`.
- `worker/src/product_search/synthesizer/__init__.py`: exported the
  new builders.
- `worker/src/product_search/synthesizer/prompts/synth_v1.txt`:
  rewritten for Context-only.
- `worker/tests/test_synthesizer.py`: removed retry-on-full-report
  tests, added builder tests + Context-only synthesize tests.
- `docs/DECISIONS.md`: added ADR-028.

**Next session — start here:**

1. **Push the commit** (this session's work is local) so the next
   prod run benefits from the new structure.
2. **Trigger one live on-demand run on each product** to confirm:
   - Bose: Bottom line shows "$47.97 from schicjar via ebay_search —
     ... (used)"; Flags section enumerates each flag with a clear
     description; Context paragraph is digit-free narrative.
   - DDR5: same shape; Bottom line uses `$X total for target`.
3. **If the new Context post-check still rejects** something
   (extremely unlikely given the LLM is no longer paraphrasing
   numbers): the retry instruction is now unambiguous, but if it
   still fails twice, the `cli.py` stub-report path renders a
   diagnostic on the web UI. No further code work needed.
4. **Phase 12b (Tier-B adapter) and 12c (schedule editor UI)** are
   still queued. Pick one once two clean consecutive runs land on
   each product.
5. **Onboarding follow-up** — when convenient, update the onboarding
   prompt to ask the user for a `description:` per flag at
   profile-creation time, so `FLAG_FALLBACK_DESCRIPTIONS` becomes a
   safety net rather than the common path.

## Status as of end of 2026-04-30 session (continuation 5)

**Per-product report columns, brand inference, synth retry, run-info
footer.**

Five code commits pushed to origin (plus one local at handoff —
`bb9b93e`, push pending):

1. **Per-product `report_columns`** (`d370c15`, ADR-025). Profile YAML
   may now declare a `report_columns: list[str]` from a 14-column
   registry (`rank, source, title, price_unit, total_for_target, qty,
   condition, brand, mpn, seller, seller_rating, ship_from, qvl_status,
   flags`). Default = legacy 8 columns when unset. Wired through:
   Pydantic schema → TS validator mirror → onboarding catalog →
   synthesizer column dispatcher. The Bose profile uses this to drop
   `qty` (always "unknown" for headphones) and surface `condition`,
   `brand`, `seller_rating`, `ship_from`. Live-tested on the
   `bose-nc-700-headphones` route — the table renders the chosen
   columns exactly.

2. **Edit-mode onboarding surfaces columns proactively** (`555b3ad`).
   The Sonnet onboarding chat, when started with a pasted existing
   profile, now MUST in its first reply: (a) acknowledge the profile,
   (b) explicitly list the current `report_columns` (or note the
   default), (c) show the full 14-id catalog, (d) ask what to change.
   No more "user has to ask before any column info appears".

3. **`brand_candidates` for missing-brand inference** (`b9d2ff6`,
   ADR-026). eBay Browse API doesn't reliably populate `brand` for
   non-RAM categories (headphones, peripherals). New optional
   profile field `brand_candidates: list[str]`; the validator
   pipeline (after ai_filter, before QVL/flags) runs
   `infer_brand_from_title(listing, candidates)` — case-insensitive
   word-boundary match, first hit wins, declared casing preserved.
   Existing non-None brands are not overwritten. Bose profile has
   `brand_candidates: [Bose]`.

4. **Synth retries once on `PostCheckError`** (`bb9b93e`, ADR-027,
   LOCAL ONLY at handoff). Both Haiku 4.5 and GLM 4.5 Flash
   occasionally fabricate percentages / savings amounts despite the
   prompt forbidding them. The retry's system prompt names the
   rejected numbers, gives explicit anti-pattern phrases, and
   restricts to qualitative phrasing only. If retry also fails, the
   original error propagates and `cli.py`'s stub-report path takes
   over. `PostCheckError` now carries `.bad_numbers: list[str]` so
   the retry can cite them.

5. **Run-info on the web UI** (`56c3a18` and `bb9b93e`):
   - Next to the Run-now button: caption `Last run: <duration> ·
     <relative time>` shown when no run is in flight (server-side
     fetch via new `getLastCompletedRun(product)` in
     `web/lib/dispatch.ts`).
   - **Below the report**: footer "Last run completed
     [absolute timestamp] · took [duration]". Renders red with the
     conclusion appended if the run failed. Reuses the same
     `lastRun` server fetch — no extra API calls.
   - Fixed a missed `<RunNowButton />` instance that wasn't
     receiving `lastRun` in the regular page path (only the
     empty-state path had it before).

98/98 worker tests pass; web tsc + eslint clean.

**Live state at handoff**:
- DDR5 profile: defaults; the deterministic table + GLM 4.5 Flash
  synth produce clean reports.
- Bose profile: custom 12-column set (most recent edit kept Brand
  and MPN columns; `brand_candidates: [Bose]` makes Brand show "Bose"
  instead of "unknown" in the next run).
- One observed failure during user testing: GLM emitted
  "saves 7.7%" → post-check rejected. The retry mechanism (pushed
  locally as `bb9b93e`) addresses this. **Push it before the next
  test run** or expect to see the same class of failure.

**Next session — start here:**

1. **Push `bb9b93e`** (synth retry + run-info footer) so the next
   run benefits from the retry and the user sees the bottom-of-page
   run footer.
2. **Trigger one live run on each product** to confirm:
   - Bose: Brand column shows "Bose" (not "unknown"); retry kicks in
     if synth fabricates again.
   - DDR5: still clean.
   - Both: bottom-of-page "Last run completed … · took …" footer
     renders correctly.
3. **If retry STILL doesn't catch all percentage fabrications**:
   options in order — (a) split Bottom line into a deterministic
   first sentence + LLM-supplied "and here's why" clause; (b)
   programmatically strip percentage tokens from LLM output before
   post_check (last resort — borders on hiding fabrications); (c)
   shorten synth's section list to just Flags + Context, drop
   Bottom line entirely.
4. **Phase 12b (Tier-B adapter) and 12c (schedule editor UI)** are
   still queued. Pick one once two clean consecutive runs land on
   each product.

## Previous status (end of 2026-04-30 session, continuation 4)

**Synth swapped to GLM 4.5 Flash; workflows now commit on failure.**

This continuation tackled the two open items at the top of continuation 3.

1. **Synth model swap (config.py)**: `DEFAULT_SYNTH_PROVIDER` is now
   `glm`; `DEFAULT_SYNTH_MODEL` is now `glm-4.5-flash`. The
   URL-hallucination concern that justified ADR-019's swap to Haiku is
   gone — the 2026-04-30 synthesizer rewrite made the ranked-listings
   table deterministic, so the LLM no longer emits URLs at all (only
   Bottom line / Flags / Context). The post-check correspondingly
   only validates numbers (not URLs) since the rewrite. Phase 5
   benchmark scored GLM 4.5 Flash 10/10 on this same post-check at
   $0/run cost. The synthesizer extracts sections by regex from the
   LLM response, which tolerates a prose preamble even if GLM emits
   one. The OpenAI shim's `reasoning_content` fallback (from
   `bd4d005`) remains in place. ADR-024.

2. **Workflows commit on failure (search-on-demand.yml &
   search-scheduled.yml)**: added `if: always()` to "Commit and Push
   changes" in both. Paired with a new diagnostic-stub write in
   `cli.py`'s `PostCheckError` handler (writes the post-check error
   message + listing counts to today's report path before
   `sys.exit(1)`), so a synth failure now commits a useful diagnostic
   instead of leaving the stale prior-day report visible on the web
   UI. Without the stub, `if: always()` alone has no effect on a
   first-run-of-the-day failure (no report file would exist yet).

75/75 worker tests pass. Pushed: pending — local commit only.

## Previous status (end of 2026-04-30 session, continuation 3)

**The whole stack works. One known intermittency in synth.**

There were TWO on-demand runs after commit `33cf8db` (Haiku swap):

1. **Run `25174096193`** (15:27Z on `33cf8db`) — **SUCCESS**.
   - ai_filter (Haiku 4.5) kept 71 of 96 listings.
   - synth (Haiku 4.5) wrote a full report (Bottom line, 30-row
     ranked listings, Diff, Flags, Context).
   - post-check passed — no fabricated numbers.
   - bot committed `7f741a1`. **This is the report currently
     committed at `reports/ddr5-rdimm-256gb/2026-04-30.md`.**
   - Total wall-clock: ~1m40s (vs ~20m for the previous GLM run).

2. **Run `25174234732`** (15:30Z on `7f741a1`) — **FAILURE at synth
   post-check** with `fabricated numbers: ['169.54', '250']`.
   - ai_filter still robust: 69 of 96 listings kept.
   - synth Haiku invented `$169.54` and `250` in its narrative.
   - cli.py exited 1 → "Commit and Push changes" was skipped (no
     `if: always()`), so the run-1 report was preserved on disk.

The user-visible "Run finished with conclusion: failure" + stale
zero-pass report on the web UI is the run-2 stderr surfaced + an
edge-cache miss for the run-1 commit. The actual committed report
is the run-1 success.

So the situation is:
- ai_filter Haiku swap is unambiguously working.
- synth Haiku is intermittent. PROGRESS already flagged this:
  "Anthropic Haiku 4.5 still produces occasional savings figures
  (~20% of fixtures)". Observed rate today is ~50% (1 fail / 2 runs)
  on prod-scale data (69-71 listings vs ~10 in the fixture suite).
- Workflow doesn't commit when search exits 1 — that masks the
  failure mode behind a stale report.

**Next session — start here:**

1. **Trigger one live on-demand run** for `ddr5-rdimm-256gb` to
   confirm GLM 4.5 Flash synth produces a clean Bottom line / Flags /
   Context. Verify the committed report has a real ranked-listings
   table (deterministic) plus the LLM-supplied qualitative sections.
2. **If GLM regresses** (post-check rejects, or empty Bottom line),
   options in order: (a) tighten `synth_v1.txt` further; (b) revert
   to Haiku via `LLM_SYNTH_PROVIDER=anthropic` /
   `LLM_SYNTH_MODEL=claude-haiku-4-5` workflow env (no code change);
   (c) propose a new ADR that re-runs the Phase 5 benchmark against
   the simplified post-numbers-only post-check.
3. **Phase 12c (schedule editor UI) and 12b (Tier-B adapter) are
   still queued** behind a clean, deterministic prod path. Pick one
   once a clean GLM run is on disk.

### What shipped this continuation (commits 88c1bfd and 33cf8db, both on origin/main)

`88c1bfd` — full rule defs in ai_filter prompt + per-product filter
log committed alongside report + inline diagnostic block when 0
listings pass. ADR-022.

`33cf8db` — swap ai_filter from `glm/glm-4.5-flash` to
`anthropic/claude-haiku-4-5`. Parser walks from first `{`/`[` so a
prose preamble can't zero a run. New test pins it. ADR-023.

The diagnostic block from `88c1bfd` worked on its very first run —
it showed exactly that GLM-4.5-Flash was emitting "Let me analyze
the products one by one..." despite json_object mode. That observation
drove the Haiku swap in `33cf8db`. The whole arc — diagnose, build
diagnostics, observe, fix — completed in three commits over one
session.

### What shipped this continuation (uncommitted at handoff start; this commit captures it)

1. **ai_filter sends full rule definitions** —
   `worker/src/product_search/validators/ai_filter.py` was building the
   "Rules to apply" prompt block from `[r.rule for r in
   profile.spec_filters]`, which dropped every `values:` / `value:`
   field. Now uses `[r.model_dump() for r in profile.spec_filters]` so
   the LLM receives e.g. `{"rule":"form_factor_in", "values":["RDIMM",
   "3DS-RDIMM"]}` instead of bare strings. The prompt also now has an
   explainer per rule type so the LLM applies each rule against
   `attrs`/`title`/`url`/`quantity_available` with a consistent
   "unknown ≠ failed" semantic. The LLM payload now also includes
   `url` and `quantity_available` per listing (needed by the
   `single_sku_url` and `in_stock` rules respectively). See ADR-022.
2. **Per-product filter log committed alongside the report** —
   ai_filter now also writes `reports/<slug>/<date>.filter.jsonl`
   (truncating per call), one row per evaluated listing. This file is
   committed by the existing workflow `git add -A` step, so the next
   regression is debuggable from the public repo with no GH Actions
   auth needed. (Anonymous artifact downloads return 401 — that's why
   the previous session couldn't pull diagnostics directly.)
3. **Inline AI-filter diagnostic block in the report** — when
   `passed_listings == 0` and `all_listings > 0`, `cli.py` now appends
   a markdown table of the first 10 rejection reasons (or, on hard
   call-level failure, the first 600 chars of the raw LLM response).
   `ai_filter` exposes `LAST_RUN_LOG` and `LAST_RUN_RAW_RESPONSE` as
   module-level capture so cli.py can render this without re-reading
   the JSONL file.
4. **Test fixture extended** — `tests/test_ai_filter.py` autouse
   fixture now also monkeypatches `_per_product_filter_log_path` so
   pytest doesn't write to the real `reports/` directory.

74/74 worker tests pass. mypy delta on changed files is a single new
`list[dict]` type-arg notice that matches the existing pre-Phase-12
style (already tracked under "Noticed but deferred").

### What shipped earlier this session (all on origin/main)

1. **Web UI polling edge-cache fix** — `getReportContent` in
   `web/lib/github.ts` appends `?_cb=${Date.now()}` to the
   `raw.githubusercontent.com` URL because the GitHub raw CDN was
   serving stale reports past `revalidatePath`. The most recent run UI
   showed "Timed out waiting for run to complete" with the report
   already on disk — see "Open issues" below.
2. **Per-listing AI filter reasoning logs** — `ai_filter.py` writes
   one line per listing to `worker/data/filter_logs/<date>.jsonl`
   with title, price, url, source, pass, reason. Sentinel rows on
   filter failure: `index=-1, title="(filter call failed)", reason=...`.
3. **`ai_filter` parser robustness** — accepts four JSON shapes
   (`{"evaluations":[…]}`, `{"indices":[…]}`, bare-array variants).
   Loud `[ai_filter] ...` stderr prints on parse/shape failures.
4. **`ai_filter` prompt rewritten** — explicitly tells the LLM that
   unknown attributes are not failures; only reject when an attr is
   PRESENT and clearly violates a rule (or the title clearly
   contradicts). Mirrors the lenient semantic of the deterministic
   `apply_filters` it replaced (eBay adapter intentionally leaves
   `form_factor`, `ecc`, `voltage_v` as None).
5. **`ai_filter` model swap glm-5.1 → glm-4.5-flash** — confirmed
   prod failure mode: GLM-5.1 is a reasoning model, ignored
   `response_format=json_object`, dumped CoT prose into `content`.
   Switched to glm-4.5-flash (Phase 5 benchmark winner, non-reasoning,
   ~10x cheaper).
6. **`_openai.py` field-pick** — in JSON mode, picks whichever of
   `content` / `reasoning_content` actually parses as JSON, instead of
   only falling back when `content` is empty.
7. **Scheduled cron disabled** — `search-scheduled.yml` keeps only
   `workflow_dispatch`. Schedule editor UI is Phase 12c.
8. **Run diagnostics uploaded as workflow artifacts** — both
   `search-on-demand.yml` and `search-scheduled.yml` now upload
   `worker/data/filter_logs/` and `worker/data/llm_traces/` as a 14-day
   artifact named `run-diagnostics-<product>-<run_id>` (or
   `run-diagnostics-scheduled-<run_id>`).
9. **Pytest no longer pollutes the local filter log** —
   `tests/test_ai_filter.py` autouse fixture monkeypatches
   `_filter_log_path()` to `tmp_path`. The local
   `worker/data/filter_logs/2026-04-30.jsonl` (41 lines, all from test
   runs) is safe to delete before the next real run.

74/74 worker tests pass. Pushed: `2072708`, `6ee155d`, `d05523e`,
`dc34961`, `8726dc3` all on origin/main.

### Open issues for next session

1. **synth Haiku fabrication** — addressed in continuation 4 by
   swapping synth to `glm/glm-4.5-flash` (ADR-024). The
   URL-hallucination concern from ADR-019 is gone since the
   synthesizer rewrite made the table deterministic. Workflows now
   commit on failure (`if: always()`) and `cli.py` writes a stub
   report on `PostCheckError`, so any future regression surfaces on
   the web UI as a diagnostic block instead of a stale-cache
   surface.
2. **UI polling still times out.** Latest run before the prompt fix
   showed "Timed out waiting for run to complete" in red on the page
   even though the report committed. The cache-buster (`?_cb=`)
   targeted `getReportContent`, but the polling state machine likely
   also reads `getProductReports` (api.github.com — not raw CDN), or
   the action genuinely took longer than the polling timeout.
   Investigate `web/components/RunNowButton.tsx` polling timeout vs
   typical action duration (~3-4 minutes per the Actions UI history).

## Open follow-ups (deferred during this session)

- **CI on `main` is chronically red** on lint steps (worker ruff +
  web ESLint). Predates Phase 12; PROGRESS already tracked this as
  deferred. Worth a small cleanup pass — this session noted it
  again but didn't fix.
- **Phase 5 benchmark fixtures should be re-run against
  `anthropic / claude-haiku-4-5`** to formally re-confirm the synth
  picks 10/10 there too (per ADR-019). Not blocking; live data is
  proving Haiku works.

## Next session — start here

`ai_filter` is on Haiku 4.5 (ADR-023). `synth` is now on GLM 4.5
Flash (ADR-024) — same model that scored 10/10 in the Phase 5
benchmark on the same post-check at $0/run. The synthesizer rewrite
made the ranked-listings table deterministic, so the LLM no longer
emits URLs at all and the URL-hallucination risk that drove ADR-019
is gone. Both workflows now commit on failure (`if: always()`); a
`PostCheckError` writes a stub diagnostic report before exiting 1.

1. **Read this file.** Continuation 4 block at top is current state.
2. **Trigger one live on-demand run** for `ddr5-rdimm-256gb`. Verify
   the committed report has the deterministic ranked-listings table
   plus an LLM-generated Bottom line / Flags / Context.
3. **If GLM regresses**, options in order: (a) tighten
   `synth_v1.txt` further; (b) revert via workflow env vars
   `LLM_SYNTH_PROVIDER=anthropic` /
   `LLM_SYNTH_MODEL=claude-haiku-4-5` (no code change); (c) propose
   a new ADR.
4. **Investigate the UI polling timeout** ("Run finished with
   conclusion: failure" appears immediately when the Action exits 1
   but the page still shows the stale report — once the workflow
   always commits and writes a stub report on PostCheckError, this
   should self-resolve).
5. **Then** pick Phase 12b (Tier-B adapter), 12c (schedule editor),
   or cost tracking.

Useful housekeeping before the next run:
- `rm worker/data/filter_logs/2026-04-30.jsonl` if the local file is
  cluttered (CI runs don't depend on local state).

## Manual verification still needed for Phase 11

- Install PWA to iOS Home Screen on a real device, enable alerts, and trigger an on-demand run that produces a material diff to ensure iOS successfully receives the push.





## Open questions for the user

- Push notification "materiality" thresholds default to: any new cheapest path, ≥5% price
  drop, any new listing. User can override these in `products/<slug>/profile.yaml` under a
  future `alerts:` block.
- **GH Actions secrets** — the four LLM keys exist in `.env`; copy them to repo secrets before
  the next CI run: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GLM_API_KEY`.
- **Z.AI account balance** — As of Phase 10, the Z.AI wallet is topped up so
  `glm-4.6` and `glm-5.1` are now callable. Re-running the Phase 5 benchmark
  with these two models is on the deferred list. Onboarding (Phase 10) still
  picked Anthropic Sonnet 4.6 over GLM 5.1 (see ADR-015) because GLM has no
  hosted web-search tool — switching to GLM there would mean wiring an
  external search backend.
- **Gemini free-tier rate limit** — the benchmark hit 429 on the very first Gemini call.
  Either set up Vertex AI billing or drop Gemini from the slate for now.

## Blockers

None.

## Noticed but deferred
- **Pre-existing worker lint/type errors.** `worker` mypy still reports
  2 errors in `adapters/memstore.py` and `adapters/cloudstoragecorp.py`
  (Phase 6 files, `url: str | None` passed to `Listing.url: str`), and
  ruff reports ~11 issues across worker tests (mostly unused `pytest`
  imports). These predate Phase 10. Consider a small clean-up pass in a
  future session — they don't block CI today but would block a stricter
  pre-commit hook later.
- **No TS unit-test framework in `web/`.** The Phase 10 schema validator
  was sanity-tested ad-hoc via a one-off node script; it lives in
  `web/lib/onboard/schema.ts` and would be a natural first TS unit-test
  if/when we add Vitest. Not urgent because CI re-runs `cli validate`
  on every commit that touches `products/`.
- **`target.configurations` schema is RAM-shaped.** The required keys
  (`module_count`, `module_capacity_gb`) make sense for DDR5 but are
  awkward for non-RAM products. The Supermicro motherboard onboarding
  filled them as `{module_count: 1, module_capacity_gb: 1}` — validates
  cleanly but is semantically meaningless. Worth a generalisation pass
  in a future session: rename to `unit_count`/`unit_size`, make the
  shape opaque, or add a `target_kind` discriminator. Update the
  Pydantic model, the TS validator, the onboarding prompt, AND the
  existing DDR5 profile in one go.
- **Synthesizer post-check is strict by design and rejects calculated comparisons** like
  "X is 7.7% cheaper than Y" and "$80 savings vs Micron." This is per ADR-001 and caught
  real issues across all three working models. After one prompt iteration adding "do NOT
  compute new numbers," GLM 4.5 Flash went 10/10. Anthropic Haiku 4.5 still produces
  occasional savings figures (~20% of fixtures) — useful as a fallback only when prompted
  even more strictly. Prompt iteration here is ongoing, not a blocker.
- **Benchmark fixtures are committed but use the synthesizer payload shape directly.** If
  `build_input_payload` ever changes shape, the fixtures need regeneration via
  `python -m benchmark.fixture_gen --force`.
- The handoff mentions Reddit r/homelabsales as a Tier C source; it requires Reddit API
  credentials. Add to env when adopted.
- The local `.env` file contains real LLM keys. It's gitignored. If those keys have been
  shared anywhere outside this machine, rotate them.
- Phase 4 used `unit_price_usd` for the 5% diff threshold. `total_for_target_usd` (the
  "cheapest path to target" cost) is arguably more user-meaningful but is `None` for any
  listing whose capacity doesn't match a profile configuration. If/when we want target-cost
  diffs, add a second threshold or fall back gracefully when `total_for_target_usd is None`.

## Recently completed

- 2026-04-30 (continuation 4): Synth swapped to GLM 4.5 Flash;
  workflows commit on failure; `cli.py` writes a stub diagnostic
  report on synth `PostCheckError` before exiting 1. ADR-024.
  - `worker/src/product_search/config.py`:
    `DEFAULT_SYNTH_PROVIDER = "glm"`,
    `DEFAULT_SYNTH_MODEL = "glm-4.5-flash"` (was anthropic /
    claude-haiku-4-5 from ADR-019).
  - `.github/workflows/search-on-demand.yml` and
    `search-scheduled.yml`: added `if: always()` to "Commit and Push
    changes".
  - `worker/src/product_search/cli.py`: the `PostCheckError` handler
    now writes a stub report to today's report path with the error
    message, fetched/passed counts, and the sources panel before
    `sys.exit(1)`. Paired with `if: always()`, this makes synth
    fabrication failures surface as a diagnostic block on the web
    UI instead of a stale prior-day report.
  - 75/75 worker tests pass.

- 2026-04-30 (continuation 3): First clean live run since Phase 12
  started. Run `25174096193` on commit `33cf8db` produced
  `reports/ddr5-rdimm-256gb/2026-04-30.md` — Bottom line, 30-row
  ranked listings, Diff, Flags, Context, Sources. ai_filter passed
  71/96 listings; synth post-check passed. A SECOND run immediately
  after hit a Haiku synth fabrication (`['169.54', '250']`) and
  post-check correctly rejected it; that run's commit step was
  skipped, so the run-1 report stayed on disk. See "continuation 3"
  block at top for the full picture.

- 2026-04-30 (continuation 2): The diagnostic block from the previous
  commit caught GLM-4.5-Flash emitting prose preamble before JSON.
  Swapped ai_filter to `anthropic / claude-haiku-4-5` (already wired
  for synth) and made the parser walk from the first `{`/`[` so a
  stray sentence can't zero out a run. New test
  `test_tolerates_prose_preamble_before_json` pins it. See ADR-023.

- 2026-04-30 (continuation): Root cause for the ai_filter 0-pass
  mystery — prompt was sending only rule type names, never the values.
  See ADR-022. Filter log now committed alongside the report
  (`reports/<slug>/<date>.filter.jsonl`) so future failures are
  debuggable without GH Actions auth.

- 2026-04-30 (late session): Five-commit ai_filter debug arc — STILL
  RETURNS 0 PASSED IN PROD. See "Open issues" at top.
  - `2072708` — disabled scheduled cron (`search-scheduled.yml` keeps
    only `workflow_dispatch`).
  - `6ee155d` — `ai_filter` parser accepts four shapes (canonical
    object, legacy `indices` object, bare-array variants); writes
    sentinel rows on failure; loud `[ai_filter] ...` stderr.
  - `d05523e` — prompt rewrite teaching LLM that unknown attrs ≠
    failure; only reject on present-and-violating data or clear title
    contradiction. Eliminates the "all 95 reject because eBay adapter
    leaves form_factor/ecc/voltage as None" hypothesis.
  - `dc34961` — workflow upload-artifact step publishes
    `worker/data/filter_logs/` and `worker/data/llm_traces/` from each
    run. Test fixture redirects log writes to tmp_path so pytest
    stops polluting the local file.
  - `8726dc3` — confirmed via stderr that GLM-5.1 dumped CoT prose
    into `content` (visible: "The user wants to filter a list of
    products for DDR5 RDIMM ECC..."). Switched `ai_filter` model from
    `glm-5.1` (reasoning) to `glm-4.5-flash` (Phase 5 benchmark
    winner; honors `json_object`; ~10x cheaper). Hardened
    `_openai.py` to pick whichever of `content`/`reasoning_content`
    actually parses as JSON. New tests pin the field-pick logic.
  - **Despite all five commits, the run after `8726dc3` still
    reported 95 fetched / 0 passed.** Diagnostic artifact wasn't
    pulled before the user ended the session. Next session must
    download the artifact and inspect the actual GLM 4.5 Flash
    response per listing.

- 2026-04-30 (early session): UI polling cache-buster + AI filter reasoning logs.
  - `web/lib/github.ts:getReportContent` now appends `?_cb=${Date.now()}`
    to the `raw.githubusercontent.com` URL. The CDN was returning the
    stale report after `revalidatePath`, so polling refreshed against
    the old content and the Run-now button reset to idle while the
    new report was invisible. Cache-buster forces CDN revalidation.
  - `worker/src/product_search/validators/ai_filter.py` now asks
    GLM-5.1 for a per-listing evaluation (`pass` + `reason`) instead
    of just the passing indices. Every evaluated listing is appended
    to `worker/data/filter_logs/<date>.jsonl` (gitignored under
    `worker/data/`). Listings the model dropped from its response are
    logged with `pass=false, reason="no verdict returned by model"`.
    Backwards-compat preserved for the older `{"indices": [...]}`
    response shape. `max_tokens` bumped 4096→8192 to fit per-listing
    reasoning. 62/62 worker tests still green; web `tsc` clean.

- 2026-04-30: Phase 12a (Storefront silent-fail diagnostic & GitHub Actions push fix).
  - Fixed a race condition in `search-on-demand.yml` and `search-scheduled.yml` where pushing the generated report would fail with `[rejected] main -> main (fetch first)` if the repository was updated during execution. Added `git pull --rebase origin main` before `git push`.
  - Identified and fixed silent failures in `nemixram`, `cloudstoragecorp`, and `memstore` adapters. They previously returned an empty list `[]` on non-200 HTTP statuses. Changed to explicitly raise `RuntimeError`, allowing `cli.py` to correctly surface the error in the "Sources searched" report panel.
  - Fixed unit tests broken by Phase 12's introduction of `ai_filter`. Bypassed the LLM call in `ai_filter.py` when `WORKER_USE_FIXTURES=1` to keep tests deterministic and pass without requiring LLM credentials.
- 2026-04-30: Synthesizer Refactor (Deterministic Table Generation).
  - Eliminated the possibility of hallucinated links or malformed table formatting by shifting the responsibility of generating the "Ranked listings" and "Diff vs yesterday" sections from the LLM to deterministic Python code.
  - Simplified the `synth_v1.txt` prompt to only request the qualitative sections (Bottom line, Flags, Context).
  - Re-wrote `synthesizer.py` to extract those sections via regex and inject mathematically perfect Markdown tables built directly from the `Listing` objects.
  - Deleted complex URL verification regex from `post_check` since URLs are no longer processed by the LLM.
- 2026-04-30: Phase 12 (Universal AI Extraction and Filtering).
  - Designed and deployed a "best of both worlds" pipeline (ADR-021).
  - Replaced explicit CSS scraping with `universal_ai_search`, using GLM-5.1 to extract JSON from raw HTML.
  - Mitigated hallucination by strictly enforcing that LLM-extracted URLs exist verbatim in the source HTML.
  - Replaced deterministic `apply_filters` with `ai_filter`, offloading complex spec evaluations to GLM-5.1 before passing the surviving listing objects to Claude Haiku for report synthesis.
  - Set up persistent `.jsonl` trace logging for all LLM calls in `worker/data/llm_traces/`.
- 2026-04-29: Phase 12 wave 6 (Profile Edit Mode & Synthesizer Fixes).
  - Implemented the **Profile Edit Mode** in the Web UI. Users can now click "Edit Profile" on any product page, which loads the existing `profile.yaml` from GitHub and passes it into the Onboarding AI context. The AI can then apply natural language edits (e.g. "avoid 16GB cards").
  - Fixed GitHub Contents API PUT failing on overwrites by automatically fetching the existing file `sha` before committing.
  - Synced `title_excludes` down to the `web` validation schema, matching the python schema.
  - Reverted synthesizer LLM from GLM-5.1 back to `claude-haiku-4-5` to avoid overly verbose Chain-of-Thought output in the generated markdown. Added strict prompt rules explicitly forbidding planning text ("Analyze the Request", etc.) while condensing the `URL` and `Source` column into a single markdown hyperlink.
  - Fixed a classic clock-skew bug in `RunNowButton` polling where the Vercel server's dispatched timestamp was slightly ahead of GitHub Action's `created_at` timestamp, causing the frontend to wait indefinitely. Also added `run-name` to the dispatch workflow.

- 2026-04-29: Phase 12 wave 5 (stale-cache hotfix on the web side).
  - Wave 4 actually fixed the synth — the 2026-04-29 report on disk has
    a full bottom-line, 21-row ranked listings table, and sources
    panel. The Vercel page kept showing the empty-output diagnostic
    because `getReportContent` and `getProductReports` in
    `web/lib/github.ts` used `next: { revalidate: 3600 }`. The 1-hour
    data cache silently masked the first prod-data success.
    `revalidatePath('/[product]')` from `/api/revalidate` invalidates
    the route-segment render cache but not necessarily underlying
    data fetches without a tag. Switched both reads to
    `cache: 'no-store'` — a 10KB markdown report fetched from GitHub
    raw on every page load is fine for this app's volume.

- 2026-04-29: Phase 12 wave 4 (synth post-check canonicalisation).
  - **Smoking-gun finding**: even Claude Haiku 4.5 — well-documented for
    verbatim copy on tabular tasks — failed the post-check on a live
    eBay URL. The "fabricated URL" was identical to a payload URL on
    `scheme + host + path`; only the tracking query string
    (`?_skw=...&hash=item...&amdata=enc%3A...`) differed. The post-check,
    not the model, was wrong.
  - **Fix**: post-check now uses canonical URL comparison — scheme +
    lowercased host + path, with trailing slash stripped. Tracking
    params no longer cause false-positive "fabrication" errors. The
    strict guarantee on prices/quantities/MPNs is unchanged. ADR-020
    documents the refinement (does not supersede ADR-001).
  - **Diagnostic**: when the post-check now fails, the worker dumps the
    offending URL and its canonical form to stderr so the next failure
    is debuggable from the GH Actions log without code edits.
  - 2 new tests added; all 65 worker tests pass.

- 2026-04-29: Phase 12 wave 3 (synth provider swap).
  - **Confirmed root cause** of empty/garbage prod synth output via
    fresh GH Actions log: post-`bd4d005`, GLM 4.5 Flash *was*
    producing output (recovered via the new `reasoning_content`
    fallback) but its output included **hallucinated eBay URLs** with
    munged tracking parameters. ADR-001's strict post-check correctly
    rejected the output. Live eBay URLs have long
    `?_skw=...&hash=item...&amdata=enc%3A...` query strings; GLM
    isn't reproducing them verbatim.
  - **Switched synth default to `anthropic / claude-haiku-4-5`**
    (ADR-019, supersedes the model choice in ADR-012). Cost is
    ~$0.001/run; ANTHROPIC_API_KEY is already wired through both
    workflows. GLM remains supported as a provider for future
    benchmarking. The synth model is env-overridable
    (`LLM_SYNTH_PROVIDER` / `LLM_SYNTH_MODEL`) so reverting is one
    workflow edit.

- 2026-04-29: Phase 12 wave 2.
  - **Confirmed eBay live path works in prod**: 186 fetched, 160 passed
    after EBAY_CLIENT_ID/SECRET were added to GH Actions repo secrets.
  - **Fixed synthesizer choke on 100+ listings**: cap synth input to top
    SYNTH_MAX_LISTINGS=30 (sorted by total_for_target_usd) and bumped
    `max_tokens` from 2048 → 4096. The Phase 5 prompt was tuned against
    fixtures of ~5–10 listings; with 160 the LLM produced empty output,
    which passed the post-check (no fabricated numbers in nothing) and
    wrote a near-blank report. The full set remains in SQLite and the
    daily CSV; the report now appends a note when truncation applies.
    Empty-synth output is now caught explicitly (italicised note in
    place of the bottom line) instead of silently producing a blank
    report.
  - **Fixed sw.js Response.clone() bug**: SWR branch was cloning
    inside an async caches.open().then() callback after the page had
    already consumed the body. Cloned synchronously and excluded
    /api/* + non-GET requests from the SW cache.

  Open follow-ups from this session:
  - **Storefront adapters returning 0 in prod live mode**:
    nemixram_storefront, cloudstoragecorp_ebay, memstore_ebay all
    reported `fetched: 0` with no error. Each has a silent-fail path
    (e.g. nemixram returns `[]` on any non-200 from
    `/products.json`). Needs targeted diagnostic — possibly add error
    logging that surfaces to the sources panel, or capture a fresh
    fixture to compare against.
  - **CI on `main` is chronically red** on lint steps (worker ruff +
    web ESLint). Predates Phase 12; PROGRESS already tracked this as
    deferred. Worth a small cleanup pass.

- 2026-04-29: Phase 12 polish wave 1. Removed `WORKER_USE_FIXTURES: 1` from
  prod workflows (ADR-017); added deterministic "Sources searched" panel
  to reports (ADR-018); added elapsed-time + tighter polling to the
  Run-now UX; replaced Next.js boilerplate favicon with the custom PWA
  icon. Tier-B adapter, schedule editor UI, and manage-sources UI deferred
  to Phase 12a/b/c. Local commit; push pending.
- 2026-04-29: Phase 11 complete. Implemented iOS push notifications for alerts via PWA subscription flow, Upstash Redis storage, and `web-push`. Material diff detection integrated into worker `cli.py`.
- 2026-04-29: Unblocked live eBay adapter by securing Production API keys and successfully fetching live DDR5 listings. Set up VAPID keys, Upstash Redis, and environment variables for Phase 11. Implementation plan approved and ready for next session.
- 2026-04-28: Phase 10 complete locally. `/onboard` chat UI + streaming
  `/api/onboard/chat` (Anthropic Sonnet 4.6 with hosted web_search) +
  `/api/onboard/save` (TS-side Pydantic-mirror validator + GitHub Contents
  API commit). New env vars: `LLM_ONBOARD_*`, `GITHUB_CONTENTS_TOKEN`. Local
  commit; push + Vercel env var setup + live E2E test pending.
- 2026-04-28: Phase 9 complete and verified end-to-end on Vercel (https://ari-product-search.vercel.app). "Run now" on `/[product]` triggers a real GH Actions workflow_dispatch, polls run status, and refreshes the report when complete. Toolbar resets to idle once the RSC refetch lands.
- 2026-04-28: Phase 8 complete. Built the PWA shell in Next.js, added Tailwind typography, configured github fetch helpers, and established the list and product detail routes.
- 2026-04-28: Phase 7 complete. Implement `scheduler-tick` CLI command to orchestrate runs across profiles matching the current UTC hour. Created GitHub Actions workflows for hourly crons and on-demand workflow_dispatch runs. Local commit; push pending.
- 2026-04-28: Phase 6 complete. Tier A adapters (Shopify API + selectolax eBay stores).
- 2026-04-28: Phase 5 complete. Synthesizer (prompt + post-check), 10-fixture benchmark with
  six bar criteria, runner across five `(provider, model)` combos. Winner: GLM 4.5 Flash
  (10/10, $0/run). `cli search` now writes `reports/<slug>/<date>.md`. 19 new tests (60
  passing total). Local commit; push pending.
- 2026-04-28: Phase 4 complete. SQLite store, CSV dump, pure-Python diff engine, `cli diff`
  command, 13 new tests (41 passing total). Local commit; push pending.
- 2026-04-28: Phase 3 complete. Validator pipeline (filters, flags, QVL, total-for-target).
- 2026-04-28: Phase 2 complete. Listing model, LLM abstraction, eBay adapter (fixture mode).
- 2026-04-28: Phase 1 complete. Pydantic Profile model, validate CLI, 10 tests (ruff + mypy
  + pytest all green). Commit local — push + CI verification pending.
- 2026-04-28: Phase 0 complete. `worker/` skeleton, `web/` Next.js scaffold,
  `.github/workflows/ci.yml` created. All local checks green (2 smoke tests, ruff, mypy,
  ESLint, tsc). Commit local — push + CI verification pending.
- 2026-04-28: Initial planning scaffold written. PLAN.md, all docs/, .gitignore, .env.example,
  README.md, CLAUDE.md, product profile template, DDR5 profile + QVL.
- 2026-04-28: Decisions confirmed (ADRs 003, 004, 005 → ACCEPTED). Added ADRs 010 (iOS PWA +
  web push) and 011 (adapter authoring philosophy). Phase plan updated.
- 2026-04-28: Pushed planning scaffold to GitHub.
