# Next session: live, naive-user verification of the onboarder + a run

## ✅ Browser MCP is now set up and validated (2026-05-29)

The blocker that prevented the previous attempt is solved. A web session now boots
ready to drive a real browser against prod:

- **`.mcp.json`** registers the `chrome-devtools` MCP (headless, isolated). Tools
  (`navigate_page`, `take_snapshot`, `take_screenshot`, `click`, `fill`,
  `fill_form`, `wait_for`, `list_network_requests`, …) appear at session start.
- **`.claude/hooks/setup-browser.sh`** (wired via `.claude/settings.json`
  SessionStart) installs headless Chromium via Playwright and publishes a stable
  symlink `/usr/local/bin/chrome-for-cdmcp` that `.mcp.json` points at.
- Validated end-to-end this session: MCP initialize OK, 29 tools listed,
  `navigate_page` → `take_snapshot` on `https://ari-product-search.vercel.app/onboard`
  returns the real onboarder UI.

**Two gotchas baked into the config — don't remove them:**
1. The container does **TLS interception**, so Chrome throws
   `ERR_CERT_AUTHORITY_INVALID` on every https site. `--acceptInsecureCerts` is in
   `.mcp.json` to get past it. (curl works without it; Chrome does not.)
2. Running as root needs `--chrome-arg=--no-sandbox` (also in `.mcp.json`).

Project `.mcp.json` servers don't auto-start in a web session without approval,
so `.claude/settings.json` sets `"enableAllProjectMcpServers": true` +
`"enabledMcpjsonServers": ["chrome-devtools"]`. **The session must be checked out
on a branch that contains these files** (`.mcp.json` + `.claude/`) — a session on
a branch without them loads no MCP. (This is why the first attempt failed: it ran
on `main`, which lacked the files.)

**First action next session:** confirm the `chrome-devtools__*` tools are present
(they load at startup). If the hook hasn't finished installing Chrome yet, re-run
`bash .claude/hooks/setup-browser.sh` then retry a `navigate_page`.

## The specific product the owner asked to test (2026-05-29)

Run the full funnel for this exact request as a naive user:

> **"DJI Neo 2 Drone Motion Fly More Combo; only 1; only new; only in stock.
> vendors: amazon, microcenter, b&H photo Video, Target, Walmart. please use them
> ALL. don't use ebay."**

Owner's ground truth: this product **IS available right now at all of these EXCEPT
Target.** Known-good detail URLs the owner supplied (use to sanity-check recall —
the onboarder should find these itself):
- Micro Center: https://www.microcenter.com/product/706337/dji-neo-2-drone-with-fly-more-combo
- Walmart: https://www.walmart.com/ip/DJI-Neo-2-4K-Drone-Fly-More-Combo-with-RC-Motion-3-Remote-Controller/19382219784
- Amazon: https://www.amazon.com/DJI-Transmission-Transceiver-Beginners-Batteries/dp/B0FJ1QH15P

So a correct run reports real in-stock listings for Amazon, Micro Center, B&H, and
Walmart, and an honest "not carried / no match" for Target — **not** a bogus
"vendor doesn't carry" for the four that do (that's exactly the ADR-124/125 fix
under test). Note: `B0FJ1QH15P` must NOT leak into `match_aliases` (ADR-116 guard).

---

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
