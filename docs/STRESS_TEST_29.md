# STRESS_TEST_29 — Live naive-user verification: DJI Neo 2 onboard + run (2026-05-29)

Owner-requested evaluation session (see `NEXT_SESSION_LIVE_VERIFY.md`). Drove prod
(`ari-product-search.vercel.app`) through the Chrome DevTools MCP as a non-technical
user, using the owner's exact request:

> "DJI Neo 2 Drone Motion Fly More Combo; only 1; only new; only in stock. vendors:
> amazon, microcenter, b&H photo Video, Target, Walmart. please use them ALL. don't
> use ebay."

**Branch reality:** the ADRs under test (122/123/124/125) are already merged to
`origin/main` (commit `303a1ad`), so prod served them — the gap the brief warned about
is closed. Verified the full funnel: interview → save (3 turns + 2 save-probes) →
saved profile → Run-now → report. Saved profile committed as `aede85a`; on-demand
report committed as `7bf2ad7` (`reports/dji-neo-2-motion-fly-more-combo/2026-05-29.*`).

**Environment caveat:** AlterLab/Scrappey looked degraded during the session — every
probe of microcenter.com and walmart.com returned `error code: 504`, repeatedly. Some
findings below are colored by that (esp. the demotion finding), but the *behavior* it
exposed is real and reproducible-on-paper.

**Framing — this is a whole-system stress test, not a DJI/4-vendor task.** The owner's
point: these specific vendors (Amazon / Micro Center / B&H / Walmart) and this specific
product are a *probe* for systemic behavior. Every finding below should be read as "what
this implies for *any* product × *any* vendor set," not "how to make these four work."
The DJI run is valuable precisely because it exercises the common hard cases at once:
a bot-walled marketplace (Amazon), a Cloudflare vendor reachable only via Scrappey (B&H),
a vendor whose *search* page returns competitor noise so only a *detail* URL works
(Micro Center), an anti-bot vendor that times out on one-shot probes (Walmart), and a
non-carrier (Target). Whatever it takes to make this reliable is roughly what it takes to
make the product reliable in general. See **"What consistent recall actually requires"**
near the end for the systemic synthesis.

---

## ✅ Works (verified live)

1. **ADR-124 — run-diagnostic correctness (HEADLINE PASS).** Amazon was bot-walled and
   fetched 0, and the report classified it as **`transient`** ("unusually small body
   (2,317 chars)… not proof the product isn't sold there… re-running may also help") and
   **`needs work`** — *not* the old misleading "Vendor doesn't carry — re-running won't
   help." This is exactly the misclassification ADR-124 fixed, now confirmed in prod.
2. **ADR-125 — Scrappey recovery (fetch layer PASS).** B&H is Cloudflare-walled; the
   report shows a live Scrappey diagnostic (`Attempt 1, 23321ms, Status 200, Len 350241,
   Trigger tier1_configured, IP 172.56.68.176 (United States)`) → surfaced the real
   **$599.00 new** listing (rank #1). Amazon's *detail* URL also fetched a full
   **494,009-char** page through the ADR-125 Scrappey/`render_js` route, proving the
   render bypass works (the failure there is downstream extraction, see ❌ G).
3. **ADR-123 — plain-English messaging.** The save-blocking validators surfaced friendly
   `userErrors`/`userWarnings` ("we have a search page but not a link to this exact
   product… ask the assistant to find amazon.com's product page"), and the post-save
   banner was clear with a prominent **"Open my product page →"** button. No jargon leaked
   to the user; the technical text stayed LLM-facing.
4. **ADR-122 — probe reuse + deterministic progress + truncation affordance.** The save
   modal reused the interview's B&H verdict ("✓ reused — already confirmed while probing
   during the interview (not re-probed)"), showed a deterministic header
   ("Probing vendor URLs — 1/3 done (attempt 1)"), and on each force-finalized turn
   rendered the plain-English "This turn ran out of time… parked in `sources_pending`" +
   a **"Keep probing the unfinished vendors"** button.
5. **ADR-121 — modal polish.** Per-host plan chips at the top, the relabeled
   "LOOKING FOR PRODUCT DETAIL PAGES ON HOSTS THAT ONLY HAVE A SEARCH URL / Capped at 3
   candidates per host. Re-trying does not add more searches," host-keyed rows (no dupes).
6. **ADR-116 — ASIN guard + detail-title gate (the half that's implemented).** The
   owner's Amazon detail `…/dp/B0FJ1QH15P` was added and probed `detailExtractable: true`
   **and** `detailTitleMatch: true`, and crucially **B0FJ1QH15P did NOT leak into
   `match_aliases`** — the exact guard the brief called out.
7. **Recall intent during the interview.** Despite microcenter/walmart 504s, the LLM kept
   all four carry-vendors as active sources and parked only Target (honest "not visible
   today") — matching the owner's ground truth at draft time. (It's the *save-time probe*
   that later eroded this — see ❌ E.)
8. **Mobile layout (390px) clean.** Header title truncates gracefully, cards stack, status
   badges + Scrappey diagnostic + cost table render with no horizontal overflow.
9. **Human-readable report polish.** `low_feedback` rendered as the tag **"Limited
   reviews"**; condition shown as "New"; per-source status badges (transient / needs work
   / ok) are scannable.

---

## ⚠️ Confusing / could be better

- **A. Long silent "Probing <host>…" during interview turns.** Each interview turn that
  probes spent ~2–3 min showing only a static "Probing www.walmart.com…" line (no
  spinner-with-elapsed, no per-vendor progress). A naive user reads this as a freeze. The
  session-cost panel also stays frozen at the previous turn's value until the turn ends,
  reinforcing the "stuck" impression.
- **B. Run-now status UX (timer resets + no auto-refresh).** **Correction:** the actual runs
  were FAST — round 2's report `generated_at` was 12:59:51, ~6–7 min after dispatch, with
  ~2 min worker compute ("Last run: 2m 09s"); round 1 similar. My earlier "~22 min / ~55 min"
  impressions were **my own observation/commit-detection lag, not the app's run time** — the
  app performed well. The two *genuine* UX nits: (1) the "Running… (Ns)" counter **resets to
  ~0 on every page reload**, so you can't read true elapsed; (2) the page **doesn't
  auto-refresh** to the finished report — you must navigate/reload to see it. With a real
  ~2 min run these are minor, but together they can make a user think the run stalled.
- **C. Dangling "Continue probing" instruction.** In the save modal, the Amazon row says
  *"ran out of time before the detail-page search could run — click 'Continue probing' to
  keep going,"* but the modal offers only **Cancel** and **Save and proceed anyway** — there
  is no "Continue probing" button. (It appears to be suppressed when there are blocking
  ADR-111 errors, which is reasonable, but the text shouldn't then tell you to click it.)
- **D. Stale "Last run" metadata on a brand-new profile.** Immediately after saving, the
  product page header showed *"Last run: 5m 12s · 8h ago"* while the body said *"No report
  yet."* Leftover run metadata from the previously-deleted same-slug product. Contradictory
  and confusing.

---

## ❌ Broken — real defects (proposed ADRs, NOT fixed this session)

### E. Save-time probe silently demotes known-good vendors to `sources_pending` on transient 504s — and never tells the user → **propose ADR-126 (P0/P1)**

The single biggest finding. During the two save-time probes, microcenter.com and
walmart.com each returned `504` and were **moved out of active `sources` into
`sources_pending`** by the probe route's `enrichedDraft`. The committed profile
(`aede85a`) ended with active sources = **Amazon (search+detail) + B&H only**;
Micro Center, Walmart, and Target all in pending. The run report then literally says
**"Pending (not yet wired): walmart.com, microcenter.com"** — i.e. they were never
searched. So of the four vendors the owner confirmed carry the product, the run covered
only two.

Why this is a real bug, not just bad luck:
- **It's silent and contradicts what the user was shown.** The LLM's chat prose said
  *"Microcenter – Search URL active (you confirmed transient timeout is acceptable)"* and
  the right-pane YAML still listed Microcenter under `sources`, but the *saved* profile had
  it in `sources_pending`. The post-save "Saved" banner warned **only about Target** — it
  never disclosed that Micro Center and Walmart were demoted.
- **The run-layer ADR-124 fix can't rescue a demoted vendor.** ADR-124/078 make the
  *runtime* retry hard and report transients honestly — but a `sources_pending` vendor is
  never fetched at runtime, so it never benefits. The demotion happens upstream, on a
  single one-shot probe, on exactly the transient (504) that ADR-124 says not to trust.
- **The pending notes are self-contradictory.** They read *"known-good host; will retry and
  auto-surface when connectivity improves,"* but pending sources aren't searched, so they
  will not auto-surface. This is the same false-promise class ADR-124 fixed at the run layer.
- **Inconsistent routing of identical failures.** On the first save-probe, walmart (detail,
  504) was *kept* in sources with an advisory note while microcenter (search, 504) was
  *demoted*. Same 504, different fate, based on search-vs-detail — undocumented and surprising.

Suggested fix direction (expanded — ADR-126 is bigger than "don't demote"): the underlying
defect is that **the one-shot probe is treated as an oracle for vendor inclusion**, when it
is in fact far weaker than the runtime escalation ladder (ADR-078) that will actually fetch
the page later. Probes 504 / return empty bodies / hit bot-walls constantly (every Micro
Center / Walmart / B&H probe failed this session) — yet the run path succeeded for B&H on the
exact same URL. So the principle should be **decouple probe outcome from inclusion**:
1. A transient probe failure (504 / timeout / empty body / bot-wall) on an
   `alterlab_known_good` host **keeps the vendor in active `sources` with an advisory note** —
   never demote to `sources_pending`. (Applies to the save-probe *and* the keep-probing path,
   Finding H.)
2. **Detail-URL acquisition must also be decoupled from probe success.** A *failed* probe of a
   known/supplied detail URL must not cause the onboarder to fall back to a search URL — it
   should keep the detail URL with a note. (This is the Micro Center failure: the probe 504'd,
   so the onboarder never locked in the `product/706337/...` detail URL and shipped only the
   `Ntt=...` search URL, which returns competitor drones.)
3. `sources_pending` is reserved for genuinely unreachable / never-carried vendors, decided by
   the *runtime* ladder over time — not by one probe.
4. Whatever the save does to a vendor (demote, downgrade, drop a URL), the post-save banner
   **must disclose it** (round 1 hid the Micro Center/Walmart demotion entirely).

Generalizes to: *for any product, a known-good vendor's reachability/recall must never hinge
on a single pre-flight probe succeeding.*

### F. ADR-116 alias-hallucination guard misses B&H product numbers → **propose ADR-127 (P1)**

`match_aliases` ended up containing **`CP.FP.00000273.01`**, lifted from the B&H URL
`…/dji_cp_fp_00000273_01_dji_neo_2_motion.html`. The ADR-116 guard caught the Amazon ASIN
(`B0FJ1QH15P` stayed out) but did **not** flag this B&H SKU — neither at the first save nor
the final one (validation returned `ok: true` with it present). The guard's
`extractSkuTokens` evidently doesn't normalize the dotted/underscore B&H product-number
form. Risk: a vendor-specific SKU in `match_aliases` can over- or under-match the carry-gate
on other vendors. Suggested fix: extend the SKU extractor to recognize B&H
`cp_fp_…` / `CP.FP.…` product numbers (and ideally any token that is a substring of a source
URL's path) and hard-error them out of `match_aliases`, same as ASINs.

### G. Amazon detail page renders but extracts 0 — recall fails downstream + token blow-up → **propose ADR-128 (P1/P2; overlaps ADR-119/120)**

The owner's known-good, in-stock Amazon detail (`B0FJ1QH15P`) fetched a **full 494,009-char
page** via the ADR-125 Scrappey/`render_js` route, but the extractor parsed **0 listings**
→ `needs work` ("the page rendered, but our reader didn't recognise this vendor's layout").
So Amazon recall failed at *extraction*, not fetch. Secondary cost issue: that detail_llm
step maxed out at **8,192 output tokens** and cost **$0.0696** — the bulk of the $0.074 run
— on a page it couldn't parse. Suggested fix direction: a deterministic Amazon detail
extractor (embedded JSON / `#corePrice` / `#productTitle`), and a guard that stops feeding
a 494KB page to the LLM at full output-token budget when the structured signal is absent.
The honest `needs work` diagnostic (ADR-109/124) is doing its job here — but the underlying
Amazon-detail recall gap is real and matches the queued ADR-119/120 territory.

---

## Notes / housekeeping

- A live product `dji-neo-2-motion-fly-more-combo` now exists on `origin/main` (created via
  the app during this eval). Left in place as the test artifact; owner can delete from the
  app if undesired. Per CLAUDE.md, tests/CI must never depend on it.
- Session onboard cost: **$0.194** across 3 interview turns (174,562 tokens; the 504 retries
  inflated input tokens). Run cost: **$0.074**. Both reasonable, though G's token blow-up and
  A/B's perceived stalls are the things a real user would feel.

---

## Round 2 — fresh delete + re-onboard (owner-requested re-run, "extend probing if offered")

Per owner ask: deleted the slug (app delete → commit `0b25bac`) and re-onboarded the
identical request, this time **exercising the extend-probing affordances** and choosing
the **bypass** path at save (different choices than round 1). Re-onboard committed as
`1b60ad0`. Results differed meaningfully — confirming the run is **non-deterministic and
infra-sensitive**, and surfacing new findings.

**What differed (round 2 vs round 1):**
- **The assistant asked a clarifying variant question** this time — "the version with RC
  Motion 3 & Goggles N3, not the standard Fly More Combo with RC-N3?" — and added a
  `title_excludes: [Drone Only, standard, RC-N3]` filter to discriminate the variant. This
  is *better* behavior than round 1 (which silently picked the variant). ✅ Good, but
  *non-deterministic* — same input, different interview path.
- **Amazon probed cleanly this time** ("48 relevance hits, Motion Fly More found") instead
  of being bot-walled — so Amazon's reachability swings run-to-run (infra/anti-bot
  variance). Micro Center and Target were kept **active** (carry-gate watched) at draft
  time rather than demoted.
- **B&H and Walmart 504'd / returned empty bodies again** and were parked in
  `sources_pending` from the start.

**Finding H — "Keep probing" can REDUCE coverage → strengthens ADR-126 (P0/P1).** I clicked
**"Keep probing the unfinished vendors"** (the extend affordance) as the owner suggested. It
worked mechanically (injected a continuation prompt; the assistant re-probed B&H + Walmart),
**but both re-probes failed again on the same transient infra** (B&H "response body too short
(0 chars)", Walmart 504), and the assistant responded by **demoting B&H from active `sources`
to `sources_pending`.** So extending the probe *lost* the one vendor that had returned a real
listing in round 1. Net-negative for recall, and exactly the transient-demotion anti-pattern
ADR-126 targets — now shown to also live in the keep-probing path, not just the save-probe.
A user who extends probing reasonably expects it to *add* coverage, never remove it.

**Finding I — the bypass path PRESERVES coverage better than the "correct" fix path.** At
save, validation blocked on Amazon + Target (search-only, need detail URLs). I chose **"Save
and proceed anyway"** (ADR-115 bypass). This **kept Amazon, Micro Center, and Target active**
and the post-save banner **did disclose both** Amazon and Target coverage gaps (good — unlike
round 1, where the silent demotion of Micro Center/Walmart was *not* disclosed). The irony to
flag: pressing the "wrong"-looking **bypass** button gave better runtime coverage than letting
the assistant "fix" things (which demotes on transient failure). That's backwards from what
the UI's affordance hierarchy implies, and reinforces ADR-126.

**Finding J — RETRACTED.** I originally wrote that the round-2 run "ran for ~55+ min." That
was wrong — it was my own commit-detection/polling lag. The report `generated_at` is
12:59:51, only ~6–7 min after dispatch (~2 min worker compute), and the run completed fine.
**The app's run performance is good; there is no severe run-latency defect.** The only real
residue is the minor status-UX in ⚠️ B (resetting timer + no auto-refresh). The
infra-degradation that IS real and directly observed is at the *probe* layer (504s / empty
bodies on Micro Center / Walmart / B&H across both rounds) — that's what drives the demotion
findings (E/H), not run latency.

**Re-confirmed across both rounds:**
- **ADR-127 (B&H alias leak):** `CP.FP.00000273.01` landed in `match_aliases` again — even
  though in round 2 B&H is only in `sources_pending`. The guard never fires for this token.
- **Finding C (dangling "Continue probing"):** save modal again told the user to "click
  'Continue probing'" with no such button present.
- **Finding D (stale "Last run"):** after delete + recreate of the same slug, the product
  page still showed "Last run: 2m 09s" from the *previous* (deleted) product's run — so
  **delete does not clear run metadata** for a reused slug.

**Round-2 RUN report (committed `e2045de`; report generated 12:59:51, ~6–7 min after
dispatch — a fast run) — the most instructive result of the session.** Zero listings passed. Per-source:
- **amazon.com — `transient`** (0 fetched): "AlterLab couldn't render this vendor's page
  this time… run this product again — usually temporary." ✅ honest (ADR-124); note the
  message differs from round 1's bot-block wording — the classifier picks the right transient
  sub-reason.
- **microcenter.com — `NO_MATCH (Mis-scoped URL; 24 listings rejected by filter)`** (24
  fetched, 0 passed): the search URL returned **Potensic ATOM drones** (ATOM 2 / ATOM SE /
  ATOM Fly More), not the DJI Neo 2. ✅ ADR-109 mis-scope diagnostic fired correctly.
- **target.com — `no match`** (17 fetched, 0 passed): returned **flight-sim hardware** (Hotas
  Warthog joystick, TCA Captain Pack, rudder pedals, T-16000M flight sticks) — unrelated.
- **`Pending (not yet wired): bhphotovideo.com, walmart.com`** — B&H was NOT searched.

Two big takeaways:
1. **The relevance filter is excellent (strong ✅).** `ai_filter` evaluated all 41 fetched
   listings and kept 0, with precise per-listing reasons ("Product is 'ATOM 2 Drone'
   (Potensic brand), not the requested 'DJI Neo 2'"; "Hotas Warthog Joystick, completely
   different"). Zero false positives — exactly the architectural commitment working.
2. **End-to-end recall was ZERO, and the cause is the demotion, not the filter.** Round 1
   surfaced the real **$599 B&H** listing; round 2 surfaced nothing — the *only* difference
   being that the keep-probing step (Finding H) demoted B&H to `sources_pending`, so the one
   vendor whose (detail) URL actually carries the product was never searched. Meanwhile the
   search URLs that *were* run (Micro Center, Target) return competitor/unrelated noise for
   this query — i.e. for this product, **detail URLs are load-bearing and search URLs are
   nearly useless**, so demoting/omitting detail URLs is fatal to recall. This is the single
   clearest argument for ADR-126 (never demote known-good detail URLs on transient failure)
   and for treating detail-URL coverage as the priority. Run cost $0.0697.

**Net read:** the onboarder UX (ADR-122/123/124/125) is genuinely good and the run-time
honesty (ADR-124) is the right design. The recall risk is concentrated at the **probe→source
routing** layer: transient probe failures (rampant under today's degraded infra) repeatedly
push owner-confirmed vendors into `sources_pending`, where they're never searched — and
neither the chat prose, the right-pane YAML, nor (in round 1) the save banner reliably tells
the user it happened. ADR-126 is the highest-leverage fix.

---

## What consistent recall actually requires (systemic view)

**Tally across both runs.** Of the four vendors that genuinely stock the product, the pipeline
surfaced a real passing listing **once** (B&H, round 1). When we actually *searched* a carrying
vendor we hit **1 for 4**; the rest were either never searched (demoted to pending) or searched
on the wrong URL.

| Vendor | What blocked detection | Layer | Does "don't demote" (ADR-126 §1) alone fix it? |
|---|---|---|---|
| **B&H** | demoted on transient re-probe (R2) | onboard routing | ✅ yes |
| **Walmart** | always demoted; runtime fetch/extract **never verified** | onboard routing **+ unverified runtime** | partial — gets it *searched*; extraction unproven |
| **Amazon** | detail page fetched 494KB but extractor parsed **0**; search bot-walled both runs | extraction (+ fetch reliability) | ❌ no — needs ADR-128 |
| **Micro Center** | only ever had a *search* URL (→ Potensic noise); detail URL never acquired because the probe 504'd | detail-URL acquisition | ❌ no — needs ADR-126 §2 (decoupled acquisition) + ADR-129 |

So **ADR-126 §1 alone gets ~2/4.** The other two fail for reasons demotion doesn't touch. To get
*consistently* 4/4 (and, generalized, reliable recall for arbitrary products/vendors) the full
chain is:

1. **ADR-126 §1 — don't demote known-good vendors on transient probe failure** (both probe paths).
   *Fixes: B&H; gets Walmart searched.*
2. **ADR-126 §2 — decouple detail-URL acquisition from probe success**: a failed probe of a
   known/supplied detail URL must keep that detail URL (with a note), not fall back to a search
   URL. *Fixes: Micro Center's mis-scoped-search root cause.*
3. **ADR-128 — Amazon detail extractor** (the page fetches; it just doesn't parse) + an
   output-token guard so a 494KB unparseable page doesn't burn the max token budget. *Fixes:
   Amazon, given its search is unreliable so the detail URL must carry it.*
4. **ADR-129 (new) — runtime mis-scope → detail-URL backfill/re-query**: when a search source
   returns all-rejected competitor noise (Micro Center's 24 Potensic hits), the run should fall
   back to the vendor's detail URL / a tighter query rather than reporting NO_MATCH and stopping.
   *Catches the case where a search URL is structurally wrong at runtime, not just at onboard.*
5. **Verify Walmart + Micro Center detail fetch/extract end-to-end under healthy infra** — these
   two have **never once** been observed fetching+parsing successfully in this session, so any
   "4/4" claim is unproven until they're seen working (Walmart relies on the ADR-106
   `__NEXT_DATA__` embedded-state path; that wasn't exercised here).
6. **Probe / infra robustness** — the upstream driver. The one-shot probe is far weaker than the
   runtime ladder; under degraded AlterLab/Scrappey it fails on vendors that the run path can
   handle. Either strengthen the probe to parity, or (cheaper) lean fully on items 1–2 so probe
   weakness stops costing recall.

**What this says about the *whole system* (not just these vendors):**
- **Recall is gated upstream, not by the filter.** The relevance filter is excellent (0 false
  positives across 41 noise listings). Every miss here was a vendor that never got fetched, or
  got fetched on the wrong URL — i.e. the **source-selection / routing layer is the weak link**
  for *any* product, not a DJI quirk.
- **Search URLs are an unreliable primitive in general.** For an even slightly ambiguous query,
  big-retailer search returns competitor or unrelated SKUs (Potensic drones, flight-sim gear).
  The system should treat a confirmed **detail URL as the primary recall mechanism** and a search
  URL as best-effort breadth — which inverts how the onboarder currently falls back (search when
  the detail probe fails).
- **One-shot pre-flight checks shouldn't gate a pipeline whose runtime is more capable.** This is
  the general lesson behind ADR-126: don't let a weaker, earlier check veto a stronger, later one.
- **Silent state changes erode trust at scale.** Any product where save quietly demotes vendors
  will under-deliver against the user's explicit "use them ALL" — the disclosure requirement
  (ADR-126 §4) is a general correctness property, not cosmetic.

**Verification plan to *claim* consistency (not done this session):** re-run the same profile
3–5× under healthy infra after items 1–4 land; expect Amazon (detail), B&H, Walmart, Micro Center
(detail) each to fetch+extract at least once; confirm none are sitting in `sources_pending` due
to a transient; confirm the report's "Sources searched" lists all four as actually searched. Only
then is "consistently 4/4" a supportable claim. Two degraded-infra runs are not enough.

## Prioritized queue (capture-only; owner prioritizes)

1. **ADR-126 (P0) — decouple probe outcome from vendor inclusion AND detail-URL acquisition.**
   (§1) Transient probe failures (504 / empty body / bot-wall) on `alterlab_known_good` hosts
   keep the vendor in active `sources` with an advisory note — never demote to
   `sources_pending`; applies to the save-probe (Finding E) **and** the keep-probing path
   (Finding H). (§2) A failed probe of a known/supplied **detail URL** must keep that detail URL,
   not fall back to a search URL (Micro Center root cause). (§4) Any save-time source change must
   be disclosed in the banner (round 1 hid the demotion). Highest-leverage fix; root cause of the
   poor end-to-end recall in both rounds.
2. **ADR-127 (P1)** — extend ADR-116 alias-hallucination guard to B&H `CP.FP.…` product
   numbers / any source-URL-derived SKU token. (Finding F; reproduced both rounds.)
3. **ADR-128 (P1)** — Amazon detail deterministic extractor (page fetches via Scrappey but
   parses 0) + LLM output-token guard on unparseable large pages. (Finding G; overlaps
   ADR-119/120.) Needed because Amazon *search* is unreliable, so the detail URL must carry it.
4. **ADR-129 (P1, new) — runtime mis-scope → detail-URL backfill/re-query.** When a search
   source returns all-rejected competitor noise (Micro Center's 24 Potensic hits → NO_MATCH),
   the run should fall back to the vendor's detail URL or a tighter query instead of stopping at
   NO_MATCH. Complements ADR-126 §2 at runtime.
5. **Verification gate (not a code change) — prove it before claiming 4/4.** Re-run 3–5× under
   healthy infra after 1–4; confirm Amazon-detail / B&H / Walmart / Micro Center-detail each
   fetch+extract and none sit in `sources_pending` from a transient. Walmart + Micro Center
   detail extraction are currently **unverified**.
6. **UX paper-cuts (P3):** run-status auto-refresh on completion + a non-resetting elapsed
   timer (B — runs are actually ~2 min, so cosmetic); remove dangling "Continue probing" text
   when the button is suppressed (C); clear stale "Last run" metadata on delete/recreate of a
   slug (D); progress feedback during long interview-turn probes (A). *(An earlier draft's
   "run-latency/stuck-run" item was DROPPED — based on my mis-measured run times; runs are fast.)*
7. **Non-determinism (note, not a defect):** identical input produced a different interview
   path/variant-handling across rounds (round 2 added a clarifying question + `title_excludes`;
   round 1 did not). Worth a deliberate stance on how much variance is acceptable.

> **Reminder (per owner):** these four vendors are a stress-test lens. The actionable output is
> the systemic fixes (routing decoupled from probes, detail-URL primacy, runtime mis-scope
> recovery, disclosure of state changes) and the verification discipline — all of which apply to
> any product × vendor set, not just DJI Neo 2.
