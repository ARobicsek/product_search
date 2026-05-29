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
- **B. Run-now perceived latency + a resetting timer.** Wall-clock from clicking Run-now to
  a visible report was **~22 min**, but the actual worker compute was **2m 09s** (report
  timestamp 12:38 PM; CSV `12-37-05Z`). The gap is GitHub-Action dispatch/queue latency.
  Two UX problems compound it: (1) the "Running… (Ns)" counter **resets to ~0 on every
  page reload**, so you can't see true elapsed; (2) the page never auto-refreshed to the
  finished report this session — it only updated after a *fresh navigation* once the report
  commit landed. A user would reasonably think the run died.
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

Suggested fix direction: a transient probe failure (504 / timeout / bot-wall) on a
`alterlab_known_good` host should **keep the vendor in active `sources` with an advisory
note** (let the runtime ladder decide), never demote to `sources_pending`. Demotion to
pending should be reserved for genuinely unreachable/never-carried vendors. And whatever
the save does to a vendor, the post-save banner must disclose it.

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

**Finding J — run latency is severe and unbounded in the UI (strengthens ⚠️ B).** The round-2
run was dispatched and, as of writing, has shown **"Running…" for ~55+ min** with no report
and no error/timeout state — the counter keeps resetting on reload and there is no "this is
taking unusually long / may have stalled" affordance. (Round 1's worker compute was only
2m 09s; the rest was dispatch/queue latency.) During this session AlterLab/Scrappey was
clearly degraded — every Micro Center/Walmart/B&H probe failed with 504 or empty body across
both rounds — which both slows runs and drives the demotion findings. **An infra-degradation
banner and a bounded "run looks stuck" state would prevent a user concluding the app is
broken.** *(If the round-2 report lands after this was written, results are appended in
`reports/dji-neo-2-motion-fly-more-combo/`; the funnel/UX findings stand regardless.)*

**Re-confirmed across both rounds:**
- **ADR-127 (B&H alias leak):** `CP.FP.00000273.01` landed in `match_aliases` again — even
  though in round 2 B&H is only in `sources_pending`. The guard never fires for this token.
- **Finding C (dangling "Continue probing"):** save modal again told the user to "click
  'Continue probing'" with no such button present.
- **Finding D (stale "Last run"):** after delete + recreate of the same slug, the product
  page still showed "Last run: 2m 09s" from the *previous* (deleted) product's run — so
  **delete does not clear run metadata** for a reused slug.

**Net read:** the onboarder UX (ADR-122/123/124/125) is genuinely good and the run-time
honesty (ADR-124) is the right design. The recall risk is concentrated at the **probe→source
routing** layer: transient probe failures (rampant under today's degraded infra) repeatedly
push owner-confirmed vendors into `sources_pending`, where they're never searched — and
neither the chat prose, the right-pane YAML, nor (in round 1) the save banner reliably tells
the user it happened. ADR-126 is the highest-leverage fix.

## Prioritized queue (capture-only; owner prioritizes)

1. **ADR-126 (P0, raised from P0/P1 after round 2)** — transient probe failures (504 /
   empty body / bot-wall) on `alterlab_known_good` hosts must **keep the vendor in active
   `sources`** with an advisory note and let the runtime ladder decide — never demote to
   `sources_pending`. Applies to **both** the save-time probe (Finding E) **and** the
   keep-probing path (Finding H). Any save-time source change must be disclosed in the
   banner (round 1 hid the Micro Center/Walmart demotion). This is the highest-leverage fix
   and the root cause of the poor end-to-end recall in both rounds.
2. **ADR-127 (P1)** — extend ADR-116 alias-hallucination guard to B&H `CP.FP.…` product
   numbers / any source-URL-derived SKU token. (Finding F; reproduced both rounds.)
3. **ADR-128 (P1/P2)** — Amazon detail deterministic extractor + LLM output-token guard on
   unparseable large pages. (Finding G; overlaps ADR-119/120.)
4. **ADR-129 (P2) — run observability** — infra-degradation banner + bounded "run looks
   stuck / unusually long" state; today a degraded-infra run shows only an open-ended
   "Running…" for 50+ min with a counter that resets on reload. (Findings B + J.)
5. **UX paper-cuts (P3):** run-status auto-refresh on completion (B); remove dangling
   "Continue probing" text when the button is suppressed (C); clear stale "Last run"
   metadata on delete/recreate of a slug (D); progress feedback during long interview-turn
   probes (A).
6. **Non-determinism (note, not a defect):** identical input produced a different interview
   path/variant-handling across rounds (round 2 added a clarifying question + `title_excludes`;
   round 1 did not). Worth a deliberate stance on how much variance is acceptable.
