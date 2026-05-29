# STRESS_TEST_30 — Phase 30 spike, Step 2: Serper.dev shopping coverage matrix (2026-05-29)

Ran `POST google.serper.dev/shopping` (gl=us, num=40) against the owner's six real
products + DJI (validated earlier). Exact `display_name` pulled from `origin/main`
profiles. **This is the COVERAGE test only (Step 2). The decisive precision-through-the-
real-filter test (Step 3) is NOT done — deferred to the implementation session.**

Cost: **2 credits per product**, 12 credits for the basket. Free tier is 2,500 credits,
so the entire basket costs ~0.5% of the free allotment. Cost is a non-issue.

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

## Verdict so far

**Step 2 coverage gate: strongly GO (6/6).** The recall problem that ~128 ADRs could not
solve by self-scraping is largely a non-problem when reading the search index. The remaining
risk is entirely downstream and *already our strength*: does the proven filter turn this
noisy-but-rich recall into clean listings (Step 3), and do term/condition/link survive on
snippet data alone. Those are the implementation session's job — not new treadmills, but
finite, testable questions against a layer (the filter) that already works.

## Next (implementation session)
Per [NEXT_SESSION_PHASE30_SPIKE.md](NEXT_SESSION_PHASE30_SPIKE.md) Step 3: adapt one
product's Serper results into real `Listing`s, run the existing `ai_filter`/validator path
offline (fixture profile + `PRODUCT_SEARCH_PRODUCTS_DIR`), and measure recall/precision.
Capture a Serper JSON fixture under `worker/tests/fixtures/serper/`. Then write the
GO/NO-GO migration call.
