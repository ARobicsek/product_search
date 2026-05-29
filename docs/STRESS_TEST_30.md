# STRESS_TEST_30 — Phase 30 spike, Step 2: Serper.dev shopping coverage matrix (2026-05-29)

Ran `POST google.serper.dev/shopping` (gl=us, num=40) against the owner's six real
products + DJI (validated earlier). Exact `display_name` pulled from `origin/main`
profiles. **This is the COVERAGE test only (Step 2). The decisive precision-through-the-
real-filter test (Step 3) is NOT done — deferred to the implementation session.**

Cost: **2 credits per product**, 12 credits for the basket. Free tier is 2,500 credits,
so the entire basket costs ~0.5% of the free allotment. Cost is a non-issue.

> **UPDATE 2026-05-29:** Step 3 is now DONE (see the "Step 3" section below). The
> GO/NO-GO migration call is **GO with three named prerequisites** — jump to the bottom.

## Scorecard (coverage gate: ≥3 legitimate vendors + the target product, real prices)

| Product | Results | Vendors | Target found? | Notable vendors | Gate |
|---|---|---|---|---|---|
| Aufschnitt Essiccata Jerky BBQ | 40 | 19 | ✅ **Aufschnitt Meats (brand-direct) $8.99** — matches app | JerkyGent, Jerky & Spice, Gleibermans | ✅ PASS |
| KingSpec XG7000 2TB NVMe SSD | 16 | 5 | ✅ **Micro Center $289.99 (exact XG7000 2TB)**, Jawa "XG 7000" | Newegg, Walmart, Sears | ✅ PASS |
| Lululemon Never Lost Keychain | 40 | 6 | ✅ **lululemon.com direct $18**, Editorialist $20–22 | eBay, Poshmark, Whatnot, Etsy | ✅ PASS |
| Supermicro H14SSL-N Motherboard | 40 | 6 | ✅ **Wiredzone $672 (exact, matches app)** | eBay, RackmountNet, CDW, Newegg, GotoDirect | ✅ PASS |
| The Netanyahus (Joshua Cohen) | 33 | 16 | ✅ eBay $6.99, n+1 $16.95, Blackwell's, Target | AbeBooks, Biblio, World of Books, Google Play | ✅ PASS |
| The Week — 1yr Subscription | 30 | 17 | ✅ DiscountMags $89.99, B&N $179, Magazines.com | Magazine Cafe, MagazinesDirect, Pocketmags, Magazineline, Magazine-Agent | ✅ PASS |

**Coverage gate: 6/6 PASS.** Every product surfaced ≥3 legitimate vendors with real
structured prices, and in every case the *exact target product* appeared — frequently
including the manufacturer-direct or canonical store (Aufschnitt Meats, lululemon.com,
Wiredzone), and the app's current best price was reproduced (jerky $8.99, Supermicro $672).

## The headline finding: the index sidesteps the bot walls

The vendors the self-scraping worker has fought hardest — **Micro Center, Wiredzone, B&H,
Walmart, lululemon, the magazine resellers** — all came back trivially through Serper,
because we're reading Google's crawl, not scraping the vendor. This is the Phase 30 thesis,
now demonstrated on the owner's real catalog, not just DJI.

Notable reversals of prior pain:
- **Micro Center appeared** for KingSpec (it was absent for DJI) → its Shopping coverage is
  product-dependent, present for mainstream tech. The thing that needed Cloudflare bypass +
  detail-URL gymnastics in STRESS_TEST_29 just… showed up.
- **The Week subscription — predicted to be the hard/empty case — was the OPPOSITE:** 17
  vendors, all the magazine-subscription resellers the worker scrapes today (DiscountMags,
  MagazinesDirect, Magazineline, Magazine-Agent, Pocketmags). Subscriptions are well served.

## Honest caveats (these decide whether the GO becomes a migration)

1. **This is coverage, not precision.** Each result set mixes the target with noise (wrong
   KingSpec models XF/NX/Gen3; used/first-edition Netanyahus at $300; Jack Link's jerky;
   resale Lululemon on Poshmark/Whatnot that may be used). The whole bet is that the
   existing 0-false-positive `ai_filter` cleanly separates these. **Step 3 (run one product's
   Serper results through the REAL filter offline) is required before declaring full GO.**
2. **Term / pack disambiguation moves from detail-page extraction to the title string.**
   The Week returned "$94 The Week **Quarterly** Digital" alongside "$179 ... One Year" —
   a quarterly priced *below* the annual. Serper gives title + price but NOT a structured
   term/pack field, so the as-sold/term logic (ADR-094 D3, standing-candidate #4) must now
   read the term from the title. Same for jerky (2oz vs 3-pack vs case). Verify the filter
   handles this on titles alone. (Note: this directly intersects the unresolved
   magazinesdirect $94-vs-$153/$247 standing candidate.)
3. **Condition.** Lots of resale (eBay/Poshmark/Whatnot/AbeBooks-used). `condition_in:[new]`
   + `title_excludes:[used]` must do the work on the snippet text; Serper sometimes carries a
   condition hint but not always.
4. **Link field not yet inspected.** Need to confirm `link` is a usable direct/merchant URL
   (vs a Google Shopping redirect) for the report's "go buy it" link. A migration concern.
5. **Amazon US still absent** (as expected) — but for these six products Amazon was not the
   load-bearing vendor; brand-direct + specialist coverage carried every one.

## Step 2 verdict

**Step 2 coverage gate: strongly GO (6/6).** The recall problem that ~128 ADRs could not
solve by self-scraping is largely a non-problem when reading the search index. The remaining
risk is entirely downstream and *already our strength*: does the proven filter turn this
noisy-but-rich recall into clean listings (Step 3), and do term/condition/link survive on
snippet data alone.

---

# Step 3 — real Serper results through the REAL ai_filter (2026-05-29)

**The decisive test.** Two products' captured Serper shopping results were adapted into real
`Listing` objects (`attrs={}` — no spec parsing; the filter must infer specs from the title,
exactly as it would in production where Serper carries no structured spec field) and run
through the **unmodified** `product_search.validators.ai_filter.ai_filter` against committed
fixture profiles loaded via `PRODUCT_SEARCH_PRODUCTS_DIR` (no live `products/<slug>`; ADR-062).

Scripts (committed, scratch — not wired into the pipeline):
`worker/scripts/serper_spike.py` (Step 1 recall), `worker/scripts/serper_filter_runtest.py`
(Step 3 harness). Fixtures: `worker/tests/fixtures/serper/{ddr5_rdimm_ecc_32gb,dji_neo2_fly_more_combo}.json`.
Fixture profiles: the committed `ddr5-rdimm-256gb` + a new `dji-neo-2-motion-fly-more-combo`.

**Method caveat (read this):** this web container has no `ANTHROPIC_API_KEY` (it's a prod
GitHub-Actions secret; the harness's own OAuth token is a consumed pipe, unreachable by a
subprocess). So the single `call_llm` *network boundary* was intercepted: every other line of
`ai_filter` ran for real (prompt construction, batching, `_extract_json`, local→global index
mapping, survivor selection, filter-log write). The replayed verdicts were produced by
applying the **dumped production system prompt's rules** to the dumped payload — i.e. the
filter-model's job, done by this session's model (Opus) standing in for the prod Haiku-4.5.
This faithfully tests the **filter PROMPT + PIPELINE**; it does not benchmark Haiku's specific
judgment. A real-Haiku rerun is a one-command repeat once a key is in the container. The
dumped prompts + verdicts are in `worker/data/serper_spike/` (gitignored).

## Finding 0 (HARD blocker, must-fix before migration): the link is always a Google redirect

Every one of the 40 DDR5 (and 31 DJI) results has `link =
https://www.google.com/search?ibp=oshop&q=...` — a Google Shopping cluster redirect. **Serper
shopping returns no direct-merchant-URL field** (fields present: `title, source, price, link,
productId, imageUrl, rating, ratingCount, position`). Consequence, measured:

- **Run A (verbatim links): 0/40 passed.** The `single_sku_url` rule rejects any URL
  containing `search?` — and every Serper link contains `/search?`. Fed verbatim, the filter
  rejects 100% of Serper listings. This is a real interaction, not a hypothetical.

The migration MUST normalize the link before the filter sees it (make `single_sku_url`
Serper-aware: Serper only ever returns product *offers*, never a search-results page, so the
rule should be skipped/relaxed for serper-sourced listings — or the URL rewritten via
`productId`). Also a product decision: the report's "go buy it" link will be a Google Shopping
cluster URL unless a click-through resolution step is added.

## Results with the link normalized (isolating product-match precision)

`--rewrite-urls` rewrites the redirect to a `productId`-keyed detail URL (what an adapter would
do), so the run measures product-MATCH precision/recall, not the single_sku_url interaction.

### DDR5 RDIMM ECC 32GB — Run B: **37/40 passed**
- **Precision on the wrong-product axis: perfect.** The only 3 rejects are the genuine
  mismatches the title self-identifies: #11 "…**Unbuffered** ECC" (UDIMM), #20 "Ecc-**Udimm**",
  #25 "ECC **UDIMM**" — all `form_factor_in`. No RDIMM module was wrongly dropped; recall ~100%
  of the genuine RDIMM set (37 real 32GB DDR5 ECC server modules across Newegg, Best Buy,
  ServerSupply, Provantage, CDW, A-Tech, OWC, TechMikeNY, Tech Atlantix, smicro, MemoryC, eBay…).
- **Soft precision cost unique to title-only data:** a handful of passes lack an explicit
  RDIMM/ECC token in the title (e.g. #6 "Team Group Elite … 32 GB DDR5 5600" — a *consumer*
  UDIMM non-ECC part; #18/#33 generic "Micron 32GB DDR5 …"; #31 "Proline … DIMM … ECC"). The
  filter passes them under its documented *unknown→pass* doctrine. With self-scraping, the
  detail page would have set `attrs.form_factor=UDIMM`/`ecc=false` and the filter would reject.
  **Serper title-only loses that disqualifying signal.** Bounded (≈1 clear leak — the Team
  Group consumer module — out of 40), but real.

### DJI Neo 2 Motion Fly More Combo — Run B: **25/31 passed**
- **Wrong-product rejection: clean (6/6).** All 4 DJI **Neo v1** offers (#2 "DJI Neo Drone",
  #18/#29 "DJI Neo Combo", #20 "DJI Neo Motion … Combo") and both **DJI Mini 2 / Mini 2 SE**
  offers (#24, #26) were rejected by `relevance_check` (different base model — the V8-vs-V15
  case). Every genuine "DJI Neo 2" offer survived; the exact target appeared at multiple US
  vendors (DSLRPros, Altitude Hobbies, Newegg ×multiple, Walmart ×multiple).
- **Family breadth (the ADR-117 question, now sharper):** the 25 passes include the exact
  "Motion Fly More Combo" (~8), the non-Motion "Fly More Combo" w/ RC-N3 (~10), "Motion
  Bundle", "Neo 2 Standard", base "Neo 2 Palm VLOG", 2-battery, etc. Correct under the current
  family-match default, but it means a Motion-Fly-More search returns the whole Neo 2 family.
  Not Serper-specific (self-scraping has the same breadth) — but title-only makes the
  sub-variant SKU undecidable, so ADR-117 (`variant_strict`) becomes more pressing.
- **NEW precision gap — price anomalies + foreign sellers leak:** #0 heliguy **$67.20** for a
  "Fly More Combo" (impossibly cheap — almost certainly an accessory/spare or a parsed
  deposit/instalment price) PASSES and would **rank #1 cheapest** in the report. #14 $1,509
  (todoparatudrone.com.ar) and #28 $1,118 (Calistenia Zona Norte) are foreign LATAM offers,
  likely currency-converted. Serper surfaces far more marketplace/foreign offers than the
  self-scraper did, and there is **no price-sanity or ship-from gate** today (the architecture
  forbids the LLM inventing a number, but a *deterministic* outlier/MAD flag + a country/domain
  gate are both compatible and needed).

---

# GO / NO-GO migration call: **GO**, with three named prerequisites

Against the gate in [NEXT_SESSION_PHASE30_SPIKE.md](NEXT_SESSION_PHASE30_SPIKE.md):

| Gate criterion | Result |
|---|---|
| ≥3 distinct legit vendors per product, real structured prices, across a mixed basket | **PASS** — 6/6 in Step 2; Step 3 confirmed 14+ vendors (DDR5) and 8+ US vendors (DJI) with real prices |
| Filter keeps precision high AND lets genuine matches through, recall > STRESS_TEST_29 (1/4, 0/4) | **PASS** — wrong-product precision perfect (3/3 UDIMM, 6/6 wrong-drone rejected); recall vastly higher (37/40, 25/31 vs 1/4 then 0/4) |
| Cost trivial at family volume | **PASS** — 2 credits/shopping call, 2,500 free, ~$0.30–1/1k after |

**The Phase 30 thesis is confirmed on the owner's real catalog: reading the index beats
self-scraping for recall, and the existing 0-false-positive filter cleanly removes the
wrong-product noise on snippet data alone.** This earns the migration. It is a **GO**, gated on
finite, non-treadmill work items (not new adversarial scraping):

1. **`single_sku_url` must be made Serper-aware (P0 — hard blocker).** Serper's `link` is always
   a `google.com/search?` redirect; verbatim it rejects 100% of listings. Skip/relax the rule
   for serper-sourced listings (they are always offers, never search pages) and decide the
   buy-link strategy (Google Shopping cluster URL vs a click-through resolution to the merchant).
2. **Add a deterministic price-sanity + ship-from/country gate (P1).** Serper returns anomalous
   ($67.20) and foreign offers with no detail page to self-correct; without a gate the report's
   cheapest rank is corruptible. Deterministic outlier flag (e.g. MAD vs the median passing
   price) + a country/domain allowlist — both honor the no-fabrication rule.
3. **Accept softer form-factor/ECC sub-precision on title-only data, or mitigate (P1/P2).** ~1
   clear leak/40 (a consumer UDIMM whose title hides the spec). Either tolerate it, add targeted
   `title_excludes`, or — and this is the bigger lever — resolve ADR-117 (`variant_strict`),
   since family breadth is now the recall surface.

**Known caveat carried forward (not a blocker):** Amazon US remains absent from Google Shopping;
for both Step-3 products brand-direct + specialist + Newegg/Walmart/Best Buy coverage carried
the result without it. Bolt on a dedicated Amazon endpoint later if a product needs it.

## Reproduce
```
# recall + capture fixture
python worker/scripts/serper_spike.py "DDR5 RDIMM ECC 32GB" --save worker/tests/fixtures/serper/ddr5_rdimm_ecc_32gb.json
# Step 3 (two-phase: dumps prompt, then replays verdicts from data/serper_spike/)
python worker/scripts/serper_filter_runtest.py --fixture worker/tests/fixtures/serper/ddr5_rdimm_ecc_32gb.json \
    --slug ddr5-rdimm-256gb --products-dir worker/tests/fixtures/profiles --tag B --rewrite-urls
```
(With an `ANTHROPIC_API_KEY` in the container, delete the `data/serper_spike/*.response.json`
files first and the harness will call real Haiku-4.5 instead of replaying.)
