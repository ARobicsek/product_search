# Decisions Log

ADR-style. One entry per material decision. New entries go at the top. Don't edit accepted decisions in place — add a new entry that supersedes if the call changes.

Status values:
- `PROPOSED` — open. Confirm or override before relying on it.
- `ACCEPTED` — settled. Don't re-debate without proposing a new ADR.
- `SUPERSEDED` — replaced by a later entry; kept for history.

---

## ADR-020 — Synthesizer URL post-check uses canonical (scheme+host+path) match

**Status**: ACCEPTED (refines, does not supersede, ADR-001)

**Context**: ADR-001 commits to "the LLM never produces a price, URL, MPN,
quantity, seller, or any other field not present verbatim in the input
JSON." The Phase 5 post-check enforced this for URLs by exact string-set
membership: every URL in the report must appear character-for-character
in the JSON-serialised input payload.

The Phase 12 prod-test on 2026-04-29 broke this twice in a row:

1. With GLM 4.5 Flash: model emitted live eBay URLs with mangled
   `?_skw=...&hash=item...&amdata=enc%3A...` query strings. ADR-019
   addressed the worst of this by switching to Anthropic Haiku 4.5.
2. With Haiku 4.5 immediately after: same failure mode — a "fabricated
   URL" rejection on a long live eBay URL. The path component matched
   an item we'd actually fetched; only the tracking-parameter string
   differed.

The destination of an eBay URL is determined by `scheme + host + path`
(e.g. `https://www.ebay.com/itm/267646680423`). Everything after the
`?` is eBay's analytics/tracking — it changes per click, per session,
per A/B bucket. Requiring the LLM to reproduce a 200-character tracking
string byte-for-byte is asking for a guarantee the model has no way to
make, since the truncation/encoding behavior of long URLs varies across
markdown renderers, the model's tokenizer, and the model's safety
shaping. The strict check was producing false-positive fabrication
errors on URLs whose *destinations* matched the payload exactly.

**Decision**: Compare URLs in the post-check by their **canonical form**
— scheme + lowercased host + path (no query, no fragment, trailing
slash stripped). A URL in the report passes if any URL in the payload
has the same canonical form. The strict guarantee that the LLM cannot
invent a destination it didn't see remains intact: a URL pointing to
`/itm/999` cannot pass unless the payload contained an item with path
`/itm/999`.

When the post-check fails, the worker now also dumps the offending
report URL and its canonical form to stderr, so future failures are
debuggable from the GH Actions log without a code change.

**Consequence**: The strict guarantee remains intact for everything
that determines the destination — host and path. False-positive
rejections from differing tracking strings disappear. The check
remains strict for prices, quantities, MPNs, and other numeric fields.
For URLs that intentionally encode product variants in the query
string (none in the current adapter slate, but possible for future
sources), this would need revisiting; that's deferred until it
actually comes up.

Tests added: `test_post_check_accepts_url_with_extra_query_params`
(real eBay URL with tracking string passes when canonical matches)
and `test_post_check_rejects_url_with_different_path` (same host but
different item ID still fails).

---

## ADR-019 — Switch synth model from GLM 4.5 Flash to Claude Haiku 4.5

**Status**: ACCEPTED (supersedes the model choice in ADR-012, Phase 5)

**Context**: ADR-012 picked GLM 4.5 Flash via Z.AI as the synth model based
on a 10-fixture benchmark scoring 10/10 with $0/run cost. Those fixtures
held 5–10 listings each with simple URLs. The Phase 12 prod-test on
2026-04-29 surfaced two related failures at live-data scale (160+
passing eBay listings):

1. The Z.AI OpenAI-compatible endpoint sometimes routes assistant text
   into `choice.message.reasoning_content` rather than `.content`. The
   provider wrapper read only `.content`, silently coalescing to `""`.
   `bd4d005` added a fallback to `reasoning_content` and stderr logging
   when both are empty. After that fix, the actual GLM output reached
   the post-check.

2. The recovered GLM output was rejected by the ADR-001 post-check for
   fabricated URLs. Live eBay item URLs include long tracking
   parameters (`?_skw=...&hash=item...&amdata=enc%3A...`), and GLM
   4.5 Flash was emitting versions with subtly modified or dropped
   query params — i.e. failing the verbatim-copy guarantee that
   ADR-001 commits to. This isn't a prompt-engineering issue; it's a
   model-quality regime gap between the benchmark fixtures and live
   data.

**Decision**: Change `DEFAULT_SYNTH_PROVIDER` from `glm` →
`anthropic` and `DEFAULT_SYNTH_MODEL` from `glm-4.5-flash` →
`claude-haiku-4-5` in `worker/src/product_search/config.py`. The
provider/model are still env-overridable (`LLM_SYNTH_PROVIDER`,
`LLM_SYNTH_MODEL`) for benchmarking and per-product overrides.
`ANTHROPIC_API_KEY` is already wired through both workflows from
Phase 10.

**Consequence**: Each daily synth run costs ~$0.001 (was $0). Across two
products with 24 scheduled ticks per day per product, that's <$0.05/month
— immaterial vs. the cost of running blind on hallucinated URLs. Haiku 4.5
has well-documented strong instruction-following on tabular verbatim
tasks; the 30-listing payload (post-truncation, ADR-pending) fits well
within its context budget. The Phase 5 benchmark fixtures should be
re-run against `anthropic / claude-haiku-4-5` as a follow-up to
formally re-confirm 10/10 there too. GLM remains supported as a provider
in case a future model version closes the verbatim-copy gap; revisit
on a benchmark when GLM 5.x lands.

**Reversibility**: Trivial. Set `LLM_SYNTH_PROVIDER=glm` and
`LLM_SYNTH_MODEL=glm-4.5-flash` in the workflow env block to roll back.

---

## ADR-018 — Sources-searched panel is deterministic, not LLM-synthesized

**Status**: ACCEPTED

**Context**: Phase 12 polish surfaced the need to show which adapters were tried
on every run (including ones that returned zero or errored), not just the
adapters whose listings survived the validator pipeline. There were two ways
to render this: (a) feed the per-source counts into the synthesizer payload
and let the LLM produce the table, or (b) build the table deterministically
in the worker and append it to the synthesized markdown.

ADR-001's post-check rejects any number in the LLM's output that doesn't
appear in the input payload. Per-source counts include numbers like 0
(error rows) which would pass the post-check, but the LLM has historically
been creative with table formatting and could re-order or mis-attribute
counts in subtle ways the post-check can't catch.

**Decision**: Build the "Sources searched" markdown table deterministically
in `worker/src/product_search/cli.py` and append it to `result.report_md`
*after* the synthesizer post-check has run on the LLM's output. The LLM
never sees per-source count data.

**Consequence**: The panel is always accurate — it's just `f"| {source.id} |
{status} | ..."` from a Python dict. The synthesizer prompt stays focused
on its narrow job (rank + bottom-line). When new adapters land, only the
worker needs to know about them; the prompt is unchanged. Trade-off: the
panel can't be reflowed by the LLM into the prose narrative — it sits as
a separate section at the bottom of the report.

---

## ADR-017 — Production runs hit live sources, not fixtures

**Status**: ACCEPTED

**Context**: Phases 0–11 baked `WORKER_USE_FIXTURES: 1` into both
`.github/workflows/search-on-demand.yml` and `.github/workflows/search-scheduled.yml`
to keep prod safe while live adapters were being stabilised. The production
report at `reports/ddr5-rdimm-256gb/2026-04-29.md` was therefore a fixture
replay (eBay item IDs `44444444`, `11111111`, etc.), not real listings.
This was a useful guard during Phases 6–9 but blocks any meaningful
prod-side validation in Phase 12.

**Decision**: Remove `WORKER_USE_FIXTURES: 1` from both prod workflows.
The env var stays supported in `_cmd_search` for local dev (`WORKER_USE_FIXTURES=1
python -m product_search.cli search ...`) and for tests, but it is not
set in CI. eBay credentials (`EBAY_CLIENT_ID`/`SECRET`) and LLM keys
remain wired through.

**Consequence**: Every scheduled hourly tick and every "Run now" click
hits the live eBay Browse API and the live storefront URLs. The eBay
Browse API has no per-call cost (5,000 calls/day free quota) and the
storefronts are public web pages. The first prod run after this change
is a real-world test of the validator pipeline against unscripted data;
breakage there is expected and a useful Phase 12 finding.

---

## ADR-016 — Replace Vercel KV with Upstash Redis

**Status**: ACCEPTED

**Context**: Phase 11 requires key-value storage for web push subscriptions. ADR-010 specified Vercel KV. However, Vercel has deprecated their first-party "Vercel KV" offering in favor of pointing developers directly to Upstash Redis via the Vercel Marketplace.

**Decision**: Provision Upstash Redis through the Vercel Marketplace and use the `@upstash/redis` client instead of `@vercel/kv`. 

**Consequence**: The environment variables injected by Vercel are prefixed with `UPSTASH_REDIS_` instead of `KV_`. The implementation plan for Phase 11 is updated to reflect the package and environment variable changes. The architecture remains functionally identical since Vercel KV was just white-labeled Upstash Redis.

---

## ADR-015 — Phase 10 onboarding model: Anthropic Claude Sonnet 4.6

**Status**: ACCEPTED

**Context**: Phase 10 needs an LLM to drive the onboarding interview, with a
strong tool-use loop for `web_search` so the model can suggest Tier B/C
sources for long-tail products (per ADR-013). Two realistic candidates were
on the table:

| Provider:Model | Tool-use quality | Web search wired? | Account ready? |
|---|---|---|---|
| `anthropic:claude-sonnet-4-6` | first-class, native | hosted server-side via Anthropic's `web_search_20260209` tool — no extra integration | yes (`ANTHROPIC_API_KEY` already in the slate) |
| `glm:glm-5.1` | unknown for tool-use loops | needs an external search backend (Tavily/Serper) wired up + a function tool | Z.AI wallet is now topped up, but never benchmarked for this call site |

The synthesizer benchmark (ADR-012) does NOT generalise to onboarding
behavior because synthesis is single-shot text formatting; onboarding is
multi-turn with tool use. Picking GLM here would mean debuting an unproven
model on the most tool-use-heavy call site, plus integrating an external
search backend before the feature works once.

**Decision**: Wire `LLM_ONBOARD_PROVIDER=anthropic` and `LLM_ONBOARD_MODEL=claude-sonnet-4-6`.
Use Anthropic's hosted `web_search_20260209` tool with `max_uses: 5` per turn
to bound cost. The `/api/onboard/chat` route reads the system prompt from the
canonical `worker/src/product_search/onboarding/prompts/onboard_v1.txt` (per
LLM_STRATEGY hard rule #3) and streams text deltas + tool-use signals as SSE
to the browser.

**Consequence**: Each onboarding session uses ~10 turns × ~2K tokens + up to
5 web searches; expected cost is in the cents per onboarding. The web app
gains a new env var (`GITHUB_CONTENTS_TOKEN`, with `contents: write` only)
for the `/api/onboard/save` endpoint that commits the new
`products/<slug>/profile.yaml`. `GITHUB_DISPATCH_TOKEN` (`actions: write`
only) is reused as a fallback. GLM 5.1 remains a re-benchmark candidate
once we have onboarding fixtures to evaluate against; revisit if Anthropic
costs become a concern or quality drifts.

---

## ADR-014 — `/api/dispatch` is gated by a browser-exposed secret

**Status**: ACCEPTED

**Context**: Phase 9 added `POST /api/dispatch`, which the "Run now" button on
`/[product]` calls to trigger an on-demand GitHub Actions run. The phase brief
says "Auth via `WEB_SHARED_SECRET` header." The same `WEB_SHARED_SECRET` is also
intended for Phase 11's `/api/push/notify`, where the worker (with the secret in
GitHub Actions secrets) calls the web app to fan out push notifications.

A browser-side caller can't keep a secret. The two natural choices were:
(a) leave `/api/dispatch` open and rely on same-origin/rate limiting, or
(b) gate it with a "shared" value that the browser also has — which exposes
the same value used by `/api/push/notify` to anyone who views the bundle.

**Decision**: Adopt (b) for now. The web app reads `WEB_SHARED_SECRET`
server-side and the browser sends `NEXT_PUBLIC_WEB_SHARED_SECRET` in the
`x-web-secret` header. In Vercel, both env vars hold the same value for now.
This matches the phase brief and keeps drive-by abuse out without inventing a
second auth scheme.

**Consequence**: When Phase 11 lands `/api/push/notify`, that endpoint MUST
NOT trust the same secret — instead it should rely on a distinct
`PUSH_NOTIFY_SECRET` (server-only, kept in Vercel + GH Actions) so that
exposing the dispatch secret in the browser bundle doesn't let an attacker
forge push notifications. Phase 11 should split the env vars at that time and
update this ADR.

---

## ADR-013 — LLM-Aided Onboarding & Web Search for Source Discovery

**Status**: ACCEPTED

**Context**: The "user enumerates sources in profile" model works well for known domains (e.g., DDR5 RAM) but doesn't generalise to long-tail products (e.g., specific handbags, rare GPUs) where sources aren't known upfront.

**Decision**: Introduce a web-search-capable LLM step *during the Phase 10 Onboarding Interview only*. The LLM will use web search to discover and suggest candidate sources/adapters to the human user. The user reviews these suggestions and, if accepted, creates deterministic adapter stubs.

**Consequence**: We adhere strictly to ADR-011: LLMs are never used for runtime data extraction. We accept the coverage gap for sites that cannot be deterministically scraped. This requires adding a 4th call site to the LLM strategy for "Onboarding source discovery (Web Search)" using a model with strong tool-use capabilities.

---

## ADR-012 — Phase 5 synthesizer model: GLM 4.5 Flash

**Status**: ACCEPTED

**Context**: Phase 5 ran the multi-vendor benchmark from [LLM_STRATEGY.md](LLM_STRATEGY.md) across the four configured providers' cheap-tier candidates, plus two Z.AI mid-tier candidates the user wanted to try. Bar: 100% on fabrication and ≥9/10 fixtures pass criteria (2)-(6). Results from `worker/benchmark/results/2026-04-28.md`:

| Provider:Model | Bar | Overall | Fab | Avg cost | p50 latency |
|---|---|---|---|---|---|
| `glm:glm-4.5-flash` | **PASS** | 10/10 | 10/10 | $0.00000 | 31.84s |
| `anthropic:claude-haiku-4-5-20251001` | fail | 8/10 | 9/10 | $0.00469 | 6.73s |
| `openai:gpt-4o-mini` | fail | 1/10 | 10/10 | $0.00051 | 7.64s |
| `gemini:gemini-2.0-flash` | fail | 0/10 | 0/10 | n/a | (rate-limited) |
| `glm:glm-4.6` | fail | 0/10 | 0/10 | n/a | (no Z.AI balance) |
| `glm:glm-5.1` | fail | 0/10 | 0/10 | n/a | (no Z.AI balance) |

`gpt-4o-mini` was 100% safe on fabrication but inconsistently dropped the "Context" section header — a model behaviour issue, not a check bug (verified by dumping raw output). Haiku 4.5 generated calculated comparisons in commentary that the post-check correctly rejected. Gemini hit 429 on the first call (free-tier exhaustion). Both `glm-4.6` and `glm-5.1` returned "余额不足或无可用资源包" — Z.AI account has free quota only for `glm-4.5-flash`.

**Decision**: Wire `LLM_SYNTH_PROVIDER=glm` and `LLM_SYNTH_MODEL=glm-4.5-flash` as the Phase 5 default. This confirms (rather than refutes) the user's hypothesis recorded in ADR-008 that GLM would win on cost.

**Consequence**: Synthesizer cost is effectively $0 on the free GLM tier. Tradeoff: ~30s p50 latency makes interactive flows feel slow — acceptable for daily scheduled runs but worth re-benchmarking if/when on-demand "Run now" UX needs to feel snappy. Anthropic Haiku 4.5 is a documented fallback for latency-sensitive paths despite its lower fabrication-pass rate (post-check still gates fabricated output).

The benchmark is re-runnable any time via `python -m benchmark.runner`. Re-run when (a) Z.AI balance is topped up so 4.6/5.1 can be evaluated, (b) Gemini billing is set up, (c) the synth prompt changes meaningfully, or (d) GLM 4.5 Flash starts failing on real reports.

---

## ADR-011 — Adapter authoring philosophy ("deterministic" ≠ "site has an API")

**Status**: ACCEPTED

**Context**: Question raised: "if the LLM is downstream of the verified data, who assembles the verified data from non-API-able sites?" Worth being explicit since it's a load-bearing distinction.

**Decision**: The LLM is never the extractor. "Deterministic" means the extraction code is written by a human, uses explicit selectors or API calls, and returns `None` when a field is absent rather than guessing. Three tiers of source mechanism, in order of preference:

1. **Real APIs** — eBay Browse API; Shopify storefronts' `/products/<handle>.json` and `/collections/<slug>/products.json` (works for NEMIX, The Server Store, and many others). Trivial adapters.
2. **Server-rendered HTML** — most eBay seller pages, Newegg, ServerSupply, Memory.net. `httpx` + `selectolax`. Adapter author saves an HTML fixture and writes CSS selectors against it.
3. **JS-rendered or anti-bot sites** — Playwright as a last resort, with a per-source rate limit and saved-fixture testing. If a source isn't worth a Playwright adapter, we skip it. Better to have fewer sources than fabricated ones.

Across all tiers: **fixtures are committed**. A test that runs against a fixture verifies the adapter's parsing logic without ever hitting the network. When a site's HTML changes, the test fails loudly — which is the correct failure mode.

**Consequence**: Adapter authoring is real engineering work, not a prompt. Each adapter is ~50-200 lines of focused code. The reward is that the data downstream of an adapter is trustworthy by construction; no LLM gymnastics required to make it safe.

---

## ADR-010 — iOS-installable PWA with web push for alerts

**Status**: ACCEPTED (per user request: "we can use this as a PWA that can alert a user in iOS")

**Context**: User wants iOS push alerts (e.g., price drops, new listings). iOS supports Web Push since iOS 16.4 (March 2023) but **only when the site is installed to the Home Screen as a PWA**.

**Decision**:
- The web app is a PWA from the start: `manifest.webmanifest` with proper icons, `display: standalone`, theme color, and a service worker. Users add to Home Screen on iOS to get the installable experience.
- Web Push uses VAPID-signed messages. Keys (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`) are environment variables; public is embedded in the client, private stays server-side.
- Push subscription storage: **Vercel KV** (free tier) keyed by a stable client ID. Single-user-shaped today; multi-user-ready if it grows.
- Trigger: the worker, after a scheduled or on-demand run, computes the diff. If material (new entrant, ≥5% price drop, new cheapest), it POSTs to a `/api/push/notify` Vercel route which fans out a Web Push to every stored subscription.
- iOS reality check: notifications only fire for installed PWAs. The UI surfaces a one-time "Add to Home Screen, then enable alerts" affordance for first-time users on iOS Safari.

This becomes Phase 11; see [PHASES.md](PHASES.md). The earlier "polish + second product proof" phase is renumbered to Phase 12.

**Consequence**: Vercel KV is one new piece of state outside the repo. Acceptable given alerts are a real user-facing feature. If we want to stay state-less, an alternative is committing the subscription set to the repo as a JSON file — works for a single user but exposes endpoints in git history; KV is cleaner.

---

## ADR-009 — Mobile-first web UI

**Status**: ACCEPTED (per user request)

**Context**: User wants to launch the system from a phone-friendly web page.

**Decision**: All UI work targets a 375px viewport first. Tables horizontally scroll within their container. "Run now" controls are thumb-reachable. No design system; Tailwind only.

**Consequence**: A phase isn't done until it's been tested at 375px. Desktop layout falls out of mobile-first naturally — no extra design work.

---

## ADR-008 — LLM provider abstraction with vendor benchmark

**Status**: ACCEPTED (per user request: "find the least expensive model that does a GOOD job")

**Context**: Four LLM API keys are available (Anthropic, OpenAI, Gemini, GLM). Different call sites have different cost sensitivity.

**Decision**: One `call_llm(provider, model, ...)` function behind which all four providers live. Phase 5 builds a benchmark harness that runs the same fixture inputs across configured models and picks the cheapest passing one. Model choice is config (env var), not code.

**Consequence**: Switching models is a secret update, not a deploy. The benchmark itself is a long-lived asset — re-runnable when a new model lands.

---

## ADR-007 — Product profile YAML as the generalization seam

**Status**: ACCEPTED (per user request: "set it up so that it could be easily used for ANY product")

**Context**: System started as RAM-specific. User wants it to handle arbitrary products.

**Decision**: Each product is a YAML profile under `products/<slug>/`. Profile declares: target, valid configurations, hard filters, soft flags, sources to query, reference data files, synthesis hints, schedule. The validator pipeline and adapters are written generically; product specifics live in profile data only. `Listing.attrs: dict` carries product-type-specific spec fields whose schema the profile defines.

**Consequence**: Adding a new product type is mostly writing a profile and (sometimes) a new source adapter. Code in `worker/src/product_search/` should never reference RAM specifically.

---

## ADR-006 — On-demand and scheduled runs both in GitHub Actions

**Status**: ACCEPTED (per user request: configurable schedule + on-demand)

**Context**: User wants daily/6-hourly/configurable cadence and ability to trigger runs manually from the web.

**Decision**: One scheduled workflow (hourly fan-out reading per-profile crons), one `workflow_dispatch` workflow keyed by product slug. Web UI calls GitHub's REST API to trigger the on-demand workflow.

**Consequence**: No separate scheduling infrastructure. Different products can run on different cadences without one-workflow-per-product proliferation.

---

## ADR-005 — Web app on Vercel, Next.js App Router

**Status**: ACCEPTED (per user confirmation)

**Context**: User mentioned Netlify and Vercel as past tools. Wants a simple mobile-friendly UI. User's Vercel projects live at https://vercel.com/aris-projects-b1e40d05 — this project will be created there.

**Decision**: Vercel + Next.js App Router. Tailwind for styling. Server-rendered routes for read paths; small API routes for `dispatch`, `onboard`, and `push/notify`.

**Consequence**: Stack is mainstream and well-supported by AI co-pilots. The Phase 8 dev session creates a new Vercel project under the user's team and links it to this repo for preview deploys per branch.

---

## ADR-004 — Worker hosted on GitHub Actions only (no separate worker service)

**Status**: ACCEPTED (per user confirmation)

**Context**: Worker needs to run on a schedule and on demand. Avoids always-on cost.

**Decision (proposed)**: GitHub Actions only. Repository is the database (committed reports). SQLite lives only inside the workflow run for diff-vs-yesterday computation.

**Consequence**: Free for public repos. No infra to manage. Tradeoff: a stand-alone worker service would let us keep a longer-lived SQLite. The plan accepts this tradeoff because reports + diff are sufficient state.

---

## ADR-003 — eBay Browse API (not HTML scraping) for the eBay adapter

**Status**: ACCEPTED (per user confirmation)

**Context**: eBay is a Tier A source. Two ways in: official Browse API or HTML scraping.

**Decision (proposed)**: Official API. Free tier ~5000 calls/day is plenty. More stable than scraping.

**Consequence**: One-time eBay developer registration. Two extra secrets in CI (`EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`). Simpler adapter code.

---

## ADR-002 — Repo-as-database; SQLite as workflow-local cache only

**Status**: ACCEPTED

**Context**: Need to persist daily reports and (for diff) yesterday's listings. Don't want to host a database.

**Decision**: Reports are markdown files committed to `reports/<slug>/<date>.md`. Listings persist via SQLite that lives inside each workflow run, populated from the previous run's CSV (also committed to a `data/` branch or to the same workflow's cache).

**Consequence**: No database hosting cost. Slight awkwardness around "yesterday's listings" — solved by either (a) committing daily CSVs to the repo or (b) using GitHub Actions cache. Plan favors (a) for auditability and (b) as fallback if commits get noisy.

---

## ADR-001 — LLM is downstream of verified data only

**Status**: ACCEPTED (architectural commitment from the handoff)

**Context**: Conversational LLMs reliably fabricate prices and stock when asked to find listings. Stricter prompts don't fix this.

**Decision**: The LLM has no web access in this system. Inputs are JSON the deterministic layer produced. Outputs are formatted markdown. Synthesizer post-check rejects any number/URL/MPN in the report that doesn't appear in the input.

**Consequence**: Cheaper models can be used (the LLM has an easy job). Failure mode "LLM made up a price" is structurally impossible. The cost is some flexibility — if a synthesizer wants to caveat a listing with information not in the input, it can't.
