# Next session — PLAN the app rebuild around the Serper recall layer

**One-line brief for the next session:**
> Working in product_search repo. Read docs/PROGRESS.md, then docs/NEXT_SESSION_REBUILD_PLANNING.md
> (and skim STRESS_TEST_30.md + ADR-130/131/132). This is a PLANNING session: design the rebuild,
> produce a plan doc + ADR, get my sign-off. Do NOT implement.

## What this session is (and is not)
- **IS:** a planning/architecture session. Output is a written rebuild plan + an ADR (e.g. ADR-133),
  reviewed and signed off before any code. Per the owner's working style: plan/ADR + sign-off
  precede non-trivial implementation; evidence-based root-cause before fixes.
- **IS NOT:** implementation. Do not start deleting the scraper or writing the Serper adapter.
- **Open with an interview** (`AskUserQuestion`) on the big forks below — they change the whole
  shape of the plan and are genuinely the owner's call. Surface backend constraints first.

## Why we're rebuilding — the Phase 30 evidence base (don't re-derive, cite)

After ~128 ADRs the diagnosis is settled and proven on the owner's real catalog:

1. **The broken layer is *sourcing/recall*, not the brains.** STRESS_TEST_29: end-to-end recall
   was 1/4 then 0/4 carrying vendors for DJI Neo 2. The relevance filter is *excellent* — 41 noise
   listings, **0 false positives**. Every miss was a sourcing miss (vendor never fetched / fetched
   on the wrong URL / fetched-but-unparseable). We were trying to *be* a web-scraping company
   against Cloudflare/Akamai/Amazon, solo — an unbounded adversarial treadmill (ADR-130).

2. **The reframe (ADR-130):** the system conflated **recall** (finding *where* a product is sold)
   with **verification** (the exact price/stock at a listing). The deterministic architecture is
   right for verification, wrong for recall. Shopping/search engines already solved
   fetch+render+parse at industrial scale and return **structured real prices** — a recall
   primitive that doesn't get bot-walled (you read the index, you don't scrape the vendor). This
   **preserves the no-fabrication commitment**: the price comes from a structured API field, never
   from an LLM's prose.

3. **The pivot is a GO (ADR-131), demonstrated on the real basket.** Serper.dev `POST /shopping`:
   Step-2 coverage **6/6 PASS** across a deliberately diverse basket (jerky, SSD, keychain,
   motherboard, used book, subscription); Step-3 recall **crushed** self-scraping — DDR5 **37/40**,
   DJI **25/31** vs the old 1/4→0/4 — with wrong-product precision perfect. 2 credits/call, 2,500
   free; cost is a non-issue. The vendors the worker fought hardest (Micro Center, Wiredzone, B&H,
   Walmart, lululemon, the magazine resellers) all came back trivially through the index.

4. **The filter model is decided (ADR-132): Haiku-4.5 @ `temperature=0`.** Full 4-model × 4-product
   bake-off (Haiku, GLM-4.6, GPT-4o-mini, Gemini-2.5-flash-lite). Per-product precision/recall:

   | Product | Haiku P/R | GLM P/R | GPT-4o-mini P/R | Gemini P/R |
   |---|---|---|---|---|
   | DDR5 | 1.00 / 0.62 | 1.00 / 0.76 | **0.00 / 0.00** | 1.00 / 0.51 |
   | DJI | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.33 | 1.00 / 1.00 |
   | The Week (subscription) | **1.00 / 1.00** | **0.73** / 1.00 | 0.00 / 0.00 | 1.00 / 0.38 |
   | Netanyahus (book) | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 0.44 | 1.00 / 1.00 |

   Haiku is the only model with precision 1.00 on all four AND a win on the ambiguous subscription.
   GLM-4.6 (the earlier lean) is disqualified as default — on the subscription it went
   non-deterministic (det 0.79, counts [9,4,11]), leaked false positives (P 0.73), and cost spiked
   6×. GPT-4o-mini rejects everything on two products. Gemini is the cost fallback (flawless
   precision/determinism, cheapest, but recall 0.38 on the subscription). **Also a standalone P0
   fix:** the prod `ai_filter` never sets `temperature`, so it runs at provider-default ~1.0 — the
   entire source of the run-to-run lottery. Set `temperature=0`.

## What the rebuild KEEPS (proven, ~70% of the codebase)
- **The no-fabrication architectural commitment** (ADR-001) — the whole reason this works.
- **`ai_filter`** (Haiku-4.5 @ temp=0) — precision is perfect on title-only data.
- **Deterministic synth** (JSON sidecar `reports/<slug>/<date>.json`; ADR-096) — synth LLM retired.
- **Validators / post-check URL+number canonicalization, report schema, storage (repo-as-DB),
  the React UI, the scheduler/trigger.**
- **Most of the onboarder** — but see the open question; sourcing-by-query may shrink it a lot.

## What the rebuild REPLACES / RETIRES (the treadmill)
- The self-scraping source layer: the `universal_ai` adapter cascade
  (AlterLab → Scrappey → curl_cffi → httpx), the bot-wall machinery in `vendor_quirks.yaml`
  (`use_scrappey`/`skip_alterlab`/`alterlab_known_good`/`force_detail_backup`), the probe
  gymnastics, the embedded-state/parser-gap tiers, per-vendor URL templates.
- Replaced by a **Serper recall adapter** behind the existing source seam: one query →
  structured offers → straight into `ai_filter`.

## The three migration prerequisites the plan MUST bake in (ADR-131)
1. **`single_sku_url` must be Serper-aware (P0 — hard blocker).** Serper's `link` is ALWAYS a
   `google.com/search?` cluster redirect; fed verbatim, the rule rejects **100%** (Run A = 0/40).
   Serper results are always *offers*, never search pages, so skip/relax the rule for serper
   sources. **Plus a product decision:** the buy-link is a Google Shopping cluster URL unless a
   click-through merchant-resolution step is added — decide the strategy.
2. **Deterministic price-sanity + ship-from/country gate (P1).** Serper surfaces anomalous
   (DJI "Fly More Combo" at **$67.20**) and foreign offers (LATAM, currency-converted) with no
   detail page to self-correct; the cheapest-rank is otherwise corruptible. A MAD-vs-median
   outlier flag + a country/domain allowlist — both deterministic, both honor no-fabrication.
3. **Title-only sub-precision / ADR-117 `variant_strict` (P1/P2).** Title-only loses the
   detail-page spec, so ~1 clear leak/40 (a consumer UDIMM whose title hides the spec) passes
   under `unknown→pass`; and family breadth (Neo 2 family vs the exact Motion-Fly-More SKU) is now
   the recall surface. Resolve ADR-117 (family-match default vs `variant_strict` opt-out) — it's
   the bigger lever and was already its own design-interview session.

## Caveats the design must account for
- **Amazon US is absent from Google Shopping** (pulled out mid-2025). Not load-bearing for the six
  tested products (brand-direct + specialist + Newegg/Walmart/Best Buy carried them). Decide:
  bolt on a dedicated Amazon endpoint now (e.g. Rainforest, PAYG, still structured) or defer.
- **Term/pack/condition move from detail-page extraction to the title string.** The Week returned
  a $94 *Quarterly* below a $179 *One Year*; jerky has 2oz vs 3-pack vs case. The as-sold/term
  logic (ADR-094 D3; the unresolved magazinesdirect $94-vs-$153/$247 case) must now read the term
  from the title. `condition_in:[new]` + `title_excludes:[used]` must do the work on snippet text.

## Open questions to resolve in the planning interview (candidate forks)
1. **Greenfield rewrite vs in-place migration behind the source seam?** In-place keeps the proven
   ~70% intact and is lower-risk; greenfield lets you shed the scraping cruft and simplify the
   data model. (Recommend in-place unless the owner wants a clean slate.)
2. **Do we still verify the winning listing's price/stock from a detail page, or trust Serper's
   structured price entirely?** This is the core recall-vs-verification fork from ADR-130. Trusting
   Serper removes the last scraping surface but accepts index-staleness; a thin verification fetch
   for *only the top-N candidates* is a middle path.
3. **How much onboarder survives?** When sourcing is a single search query, a profile becomes
   ~(query + filter spec) instead of a curated vendor list. This could massively simplify
   onboarding (no per-vendor probes, no detail-URL backfill, no bot-wall routing). Decide the new
   onboarding shape.
4. **Keep the Python worker + Next.js web split, or consolidate?**
5. **Amazon endpoint now or later?** (See caveat.)

## Setup / keys (ephemeral container — keys never persist, never committed)
The five keys (`SERPER`, `ANTHROPIC`, `GLM`, `OPENAI`, `GEMINI`) must be pasted into the start
prompt or be present as Claude Code web-environment secrets. The bake-off harness only needs the
model keys; live Serper recall needs `SERPER_API_KEY`. **Owner durable-storage action items:** add
`SERPER_API_KEY` + `ANTHROPIC_API_KEY` to GitHub Actions secrets (the worker is the prod consumer);
Vercel only if the onboarder calls them. Reproduce the bake-off:
`cd worker && set -a; . ./.env; set +a && PYTHONPATH=. python -u scripts/serper_filter_bakeoff.py --all --trials 3 --temperature 0`
(use `python -u` — the harness block-buffers stdout, so without it nothing prints until the ~20-min
run, GLM the slow leg, exits).

## Deliverable for the session
A reviewed rebuild plan (new doc, e.g. `docs/REBUILD_PLAN.md` or a Phase 31 section in PHASES.md) +
an ADR for the architecture call + owner sign-off. **No production code this session.**
