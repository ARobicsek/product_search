# Vendor reach — Bose NC 700 universal_ai sources, 2026-05-04

Per-vendor verdict for the 7 universal_ai_search URLs originally on the bose-nc-700-headphones profile, captured via `probe-url --render` (and httpx fallback when AlterLab errored). Bodies are committed to `worker/tests/fixtures/universal_ai/<vendor>-bose-2026-05-04.html`.

| Vendor | Fetcher | Status | Body | Candidates | Verdict | Action |
|---|---|---|---|---|---|---|
| amazon.com | alterlab | 200 | 1.1 MB | 78 | Renders fine; extractor works | Keep in `sources` |
| backmarket.com | alterlab | 200 | 32 KB | 0 | Cloudflare Turnstile challenge — AlterLab can't bypass | Demote after 3 consecutive 0-yield runs |
| bestbuy.com | alterlab | 200 | 6.9 KB | 0 | Geofencing — AlterLab IPs route to "Best Buy International / Select your Country" splash, not the US site | Demote after 3 consecutive 0-yield runs |
| walmart.com | alterlab→httpx | 503→200 | 15.6 KB | 1 (nav only) | AlterLab consistently 503/504 today; httpx fallback gets a stripped bot-block shell | Demote after 3 consecutive 0-yield runs |
| crutchfield.com | alterlab→httpx | 503→403 | 5.9 KB | 0 | AlterLab consistently 503/504; httpx fallback gets explicit 403 Forbidden | Demote after 3 consecutive 0-yield runs |
| reebelo.com | alterlab | 200 | 134 KB | 1 ("About Reebelo" footer) | Page renders but products are fully client-side; no product cards or `/products/` URLs in the SSR shell | Demote after 3 consecutive 0-yield runs |
| bose.com /c/refurbished | (n/a) | (n/a) | (n/a) | (17 wrong-product candidates last run) | Bose discontinued the NC 700; this URL will never carry the product | **Removed** from profile this session (Phase 19 task 3) |

## Categorisation

Three failure modes surfaced:

1. **Bot-tier defeat (AlterLab can't bypass)** — backmarket (Cloudflare Turnstile), walmart, crutchfield (intermittent AlterLab 503/504; httpx fallback gets blocked). Vendor is reachable in principle but our current fetch tier can't get past their anti-bot.
2. **Geo-routing (vendor serves wrong site)** — bestbuy (US search URL → International country-selector splash). Vendor renders, but to the wrong audience.
3. **Client-side rendering (vendor renders but SSR shell is empty)** — reebelo. The page is delivered but products only exist after JS executes against the vendor's API; we'd need a real headless browser session, not just a rendered fetch.

All three are in the same bucket for **policy** purposes: the URL is in `sources`, the run does work, the run consistently produces 0 listings, and we'd like the system to notice and downgrade rather than burn AlterLab credits + LLM tokens forever.

## Policy decision

See [ADR-040](DECISIONS.md). Tracked per-source 0-yield streaks in the SQLite store; auto-demote to `sources_pending` after **3 consecutive 0-yield runs**. Demotion is reversible — re-saving the profile via the onboarder re-evaluates each URL through the relaxed save-time gate (ADR-038).

## Out of scope here

- Replacing AlterLab with a stronger fetch tier (e.g., Bright Data, Scrapfly residential). Separate evaluation if Phase 19's vendor-reach data warrants it across multiple products.
- Per-vendor structural extractors (à la ADR-039 for Amazon) for the bot-tier-defeat vendors. Pointless until we can actually fetch them.
- The intermittent nature of AlterLab 503/504 on walmart + crutchfield deserves its own follow-up; today's verdict assumes the failure rate stays roughly where it was on 2026-05-04.
