# Next session — Phase 30 spike: shopping-API recall layer (go/no-go)

**One-line brief for the next session:**
> Working in product_search repo. Read docs/PROGRESS.md, then docs/NEXT_SESSION_PHASE30_SPIKE.md. Run the Phase 30 recall-layer spike and report the go/no-go verdict.

This is a **time-boxed proof-of-concept**, not a migration. Do not rewrite the
system. Do not touch the validator/filter/synth/UI. The entire point is to answer
ONE question with data:

> **Does a shopping-API recall layer (Serper.dev) surface the products + vendors we
> care about, with real structured prices, well enough to replace the self-scraping
> source layer?**

If yes → we plan the migration in a later phase. If no → we have earned the right to
seriously consider abandoning, on evidence rather than fatigue.

---

## Why we're here (the strategic finding)

After ~128 ADRs, STRESS_TEST_29 confirmed the system still has poor end-to-end recall
(1/4 carrying vendors in round 1, 0/4 in round 2 for DJI Neo 2). The crucial diagnosis:

- **The relevance filter is excellent** — 41 noise listings, **0 false positives**. The
  hard-seeming part (LLM won't fabricate / won't pass junk) is *solved*.
- **Every miss was a sourcing miss** — a vendor never fetched (demoted on a transient
  504), fetched on the wrong URL (search page → competitor noise), or fetched but
  unparseable (Amazon's 494KB detail page → 0 listings).
- **We have been trying to *be* a web-scraping company** against Cloudflare/Akamai/Amazon,
  vendor-by-vendor, as a solo project. That is an unbounded, adversarial treadmill
  (every vendor a new parser; every parser rots; every anti-bot update reopens the wound).

**The reframe:** the project conflated two jobs — **recall** (finding where a product is
sold) and **verification** (the exact price/stock at a listing). Our deterministic
architecture is right for verification and wrong for recall. Search/shopping engines have
*already solved* fetch+render+parse at industrial scale and return **structured real
prices** — so they're a recall primitive that doesn't get bot-walled (you read an index,
you don't scrape the vendor). This **preserves the no-fabrication commitment**: the price
comes from a structured API field, never from an LLM's prose.

Full reasoning is in this session's chat + STRESS_TEST_29.md. See ADR-130 (DECISIONS.md).

---

## Pre-work already done THIS session (don't redo)

1. **Serper.dev key obtained and stored** in local `.env` as `SERPER_API_KEY` (gitignored).
   `.env.example` documents the var. **The container is ephemeral** — if `.env` is gone in
   the next session, re-add the key from the owner (or it'll be in GitHub Actions/Vercel by
   then; see "Where the key lives").
2. **Key validated live.** Two real queries against `POST https://google.serper.dev/shopping`
   returned structured `{source, price, title, link, ...}` JSON, 2 credits per call:

   **DJI Neo 2 "Motion Fly More Combo"** — 30 results. Vendors present: **B&H Photo,
   Walmart (multiple marketplace sellers), Newegg**, plus many specialist drone shops
   (DSLRPros, Altitude Hobbies, DrDrone, heliguy, Flyingcam). Real prices ($449–$965 for
   plausible matches; plenty of noise: wrong variants, DJI Neo (v1), Mini 2, foreign
   sellers). **Absent: Amazon, Micro Center, Target** (see coverage caveats).

   **DDR5 RDIMM 256GB ECC** (the original RAM use case) — 40 results. Vendors present:
   **Newegg (many sellers), Best Buy, NEMIX RAM, ServerSupply, CDW, Provantage, A-Tech,
   xByte**. Excellent coverage; real structured prices. The original product domain is
   very well served.

   **Read on the results:** the noise (wrong variants / foreign sellers / accessories) is
   exactly what our 0-false-positive filter already eats. The architectural fit is clean:
   **Serper = recall, existing ai_filter = precision.**

3. **Confirmed coverage caveats** (these shape the design, not blockers for the spike):
   - **Amazon US is absent from Google Shopping** (Amazon pulled out of US Shopping mid-2025,
     returned internationally but not US). If Amazon matters, bolt on a dedicated Amazon
     endpoint later (e.g. Rainforest API, also PAYG) — still structured, still no fabrication.
   - **Micro Center / Target** did not appear for DJI — regional/spotty feeders. Quantify in
     the spike; don't assume.

---

## The spike (do this)

**Budget: one session. Stay behind the adapter seam. Touch nothing downstream.**

### Step 1 — A throwaway Serper recall script
Create `worker/scripts/serper_spike.py` (a scratch script, NOT wired into the pipeline):
- Read `SERPER_API_KEY` from env.
- Function `serper_shopping(query, gl="us", num=40) -> list[dict]` → POST to
  `https://google.serper.dev/shopping`, return the `shopping` array.
- Map each result to a partial `Listing`-shaped dict: `{source, url(link), title, price
  (parse "$1,234.00" → float), seller_name(source), condition(None)}`. Keep it crude — this
  is a spike, missing fields stay `None` (honors the architecture's "missing stays missing").
- Print a table + a per-vendor rollup.

### Step 2 — Coverage matrix across products
Run the script for a deliberately mixed basket (pick ~5):
- `DJI Neo 2 Motion Fly More Combo` (hard: ambiguous variant, Amazon-dependent)
- `DDR5 RDIMM 256GB ECC server memory` (the original RAM domain)
- 2–3 products the owner has actually struggled with (ask, or pull recent slugs from
  `products/` on `origin/main` after `git fetch`).
For each, record: total results, distinct vendors, how many are plausible matches by eye,
and **which owner-relevant vendors appeared**. Write it to `docs/STRESS_TEST_30.md`.

### Step 3 — Feed ONE product through the REAL filter (the key test)
The decisive test isn't "did vendors show up" — it's "does our proven filter turn this
noisy recall into clean, correct listings." So:
- Take the DJI (or a RAM) Serper results, adapt them into real `Listing` objects.
- Run them through the **existing** `ai_filter` / validator path offline (use the worker's
  test/fixture harness + `PRODUCT_SEARCH_PRODUCTS_DIR` override per CLAUDE.md — do NOT depend
  on a live `products/<slug>`; build a fixture profile).
- Measure: of the plausible-by-eye matches, how many survive the filter (recall) and how
  many junk rows leak (precision). We expect precision to stay ~perfect (it's the same
  filter); the question is whether enough *real* listings survive to be useful.

### Step 4 — Write the verdict in `docs/STRESS_TEST_30.md`

---

## Go / no-go gate (decide on these, write the call explicitly)

**GO (plan the migration)** if ALL hold:
- ≥ ~3 distinct legitimate vendors per product with **real structured prices**, across the
  mixed basket (not just one lucky product).
- The existing filter keeps precision high (few/no junk rows leak) AND lets through the
  genuine matches (recall > what self-scraping achieved in STRESS_TEST_29 — a low bar: it
  was 1/4 and 0/4).
- Cost is trivial at family volume (it is: 2 credits/shopping call, 2,500 free, then
  ~$0.30–$1/1k — confirm in-session via the `credits` field).

**NO-GO (escalate to the owner; abandon becomes a serious option)** if:
- Shopping results are systematically too sparse / too stale / too noise-dominated for the
  owner's real products, even after the filter — i.e. the recall layer can't beat what we
  already have. That's the honest "the value isn't reachable" signal.

**PARTIAL / promising** (likely outcome): great for mainstream + the RAM domain, gaps on
Amazon/Micro-Center. Then the GO plan includes a dedicated Amazon endpoint + a stance on
spotty vendors. Say so explicitly; don't hand-wave.

---

## Where the key lives (answer to "git or Vercel?")

- **NEVER git.** Real secrets are never committed (CLAUDE.md hard rule). `.env` is gitignored;
  `.env.example` only ever holds the empty placeholder.
- **This session/local:** real key is in local `.env` (ephemeral container).
- **Production worker (the primary consumer — recall runs in the GitHub Actions worker):**
  set a **GitHub Actions repository secret** named `SERPER_API_KEY`
  (repo Settings → Secrets and variables → Actions). Both search workflows will need it
  referenced once the adapter lands.
- **Vercel:** only if the web app/onboarder also calls Serper (e.g. to replace probe-time
  scraping). Add `SERPER_API_KEY` as a Vercel **Production** env var then redeploy. Not
  required for the spike.
- **Owner action items (so the next session isn't blocked):** add `SERPER_API_KEY` to
  GitHub Actions secrets now; add to Vercel later if/when the onboarder uses it. The key was
  pasted in chat, so consider rotating it from the Serper dashboard once it's in the durable
  stores (low-value key — a leak only spends your free search credits).

---

## Guardrails (read before coding)

- **This is a spike.** No production wiring, no deleting the universal_ai adapter, no schema
  changes. Scratch script + one offline filter run + a findings doc. That's the whole job.
- **Architectural commitment is non-negotiable even in the spike:** the price/seller/url come
  from Serper's structured fields. The LLM is NEVER asked to read a page or emit a number.
  If you find yourself prompting an LLM to "find listings," stop (ARCHITECTURE.md).
- **No live `products/<slug>` dependency** in any test (CLAUDE.md / ADR-062) — build a fixture
  profile and use `PRODUCT_SEARCH_PRODUCTS_DIR`.
- Capture any live Serper JSON you rely on as a committed fixture under
  `worker/tests/fixtures/serper/` (strip nothing sensitive — there are no secrets in results).
- End of session: write `docs/STRESS_TEST_30.md`, update PROGRESS.md with the verdict, log the
  outcome under ADR-130 (or a follow-up ADR), commit, push.
