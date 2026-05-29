# Next session: live, naive-user verification of the onboarder + a run

**Owner ask (2026-05-29):** Do live verification yourself — drive the onboarder
and run a product end-to-end against prod — **with a critical eye, pretending you
are a naive (non-technical) user.** Report: what works? what makes sense? what
doesn't work? what's confusing? what could be better? This is an evaluation
session, not a feature session.

## Why now

The last session shipped four UX/diagnostic changes off live DJI Neo 2
screenshots but **none are verified live**: ADR-122 (reuse interview probes at
save + deterministic progress), ADR-123 (plain-English save messages + the
"Open my product page" button), ADR-124 (`vendor_does_not_carry` no longer
preempts WATCHED/TRANSIENT/PARSER_GAP), ADR-125 (Scrappey recovery on known-good
*detail* pages + Amazon routed through Scrappey). They all live on branch
`claude/quirky-volta-L3R7L` (NOT merged to main yet).

## How to run it

- Prod app: `https://ari-product-search.vercel.app` (`/onboard` for the
  interview; the home page lists products and has Run-now).
- Drive a real browser via the Chrome DevTools MCP (the same tool ADR-108/-110
  verification used). Do a fresh onboard of a real multi-vendor product AND a
  re-onboard/edit of an existing one, then **Run** it and read the report.
- **Branch reality:** prod serves `origin/main`. To verify THIS session's
  changes live you must first get `claude/quirky-volta-L3R7L` deployed (merge to
  main or a preview deploy) — confirm with the owner before merging. If it isn't
  deployed yet, say so up front and verify against whatever IS live, noting the
  gap.

## The naive-user lens (take notes continuously, don't just pass/fail)

Walk the whole funnel as if you've never seen it:

1. **Interview** — Are the questions clear? Does it ask for things a normal
   person wouldn't know? When it says "Probing <host>…", is it obvious what's
   happening and why? (See the earlier session's Q about re-probing.)
2. **Save** — Does the save-time probe modal make sense? Are the messages
   plain English now (ADR-123)? Is the "Open my product page" button obvious?
   When something blocks save, do you understand what to do?
3. **Warnings** — Trigger a coverage warning (e.g. a vendor that only resolves
   to a search URL, or a timeout). Read it as a layperson: actionable? jargon?
4. **Run + report** — Run the product. Read "Sources searched": does each
   source's outcome message make sense and give honest, correct advice?
   Specifically confirm ADR-124/125: a bot-walled/known-good vendor should NOT
   say "Vendor doesn't carry — re-running won't help"; Amazon should recover via
   Scrappey or at worst report TRANSIENT.
5. **Whole experience** — costs, durations, mobile layout (narrow viewport is
   non-negotiable per CLAUDE.md), anything that felt slow, surprising, or wrong.

## Deliverable

A written findings report (new `docs/STRESS_TEST_*.md` or similar, newest-first)
with concrete, prioritized observations: ✅ works, ⚠️ confusing/could-be-better,
❌ broken — each with the exact screen/message and a suggested fix. Turn the
real defects into proposed ADRs and queue them in PROGRESS.md. **Do not
implement fixes this session unless trivial — capture first, the owner
prioritizes.**

## Reference

- Verified ADRs to spot-check: 122, 123, 124, 125 (DECISIONS.md).
- Prior live-verification patterns: PROGRESS.md notes for ADR-093/108/110/114/115.
- Still-queued feature defects (out of scope for this eval, but watch for them):
  ADR-117 (filter lenience), 118 (vendor-condition gate), 119 (Amazon `&i=`),
  120 (mis-scoped vs doesn't-carry subkinds).
