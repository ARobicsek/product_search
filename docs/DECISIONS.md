# Decisions Log

ADR-style. One entry per material decision. New entries go at the top. Don't edit accepted decisions in place — add a new entry that supersedes if the call changes.

Status values:
- `PROPOSED` — open. Confirm or override before relying on it.
- `ACCEPTED` — settled. Don't re-debate without proposing a new ADR.
- `SUPERSEDED` — replaced by a later entry; kept for history.

---

## ADR-054 — Tri-state card run-status (Running-since / Waiting to run / idle); never show a stale last-run time while running (ACCEPTED — implemented)

**Status**: ACCEPTED — implemented 2026-05-18 (user request, same session as the Phase 20 cron-job.org reactivation). `web/lib/dispatch.ts` (`ActiveRuns` now carries each active on-demand run's `run_started_at` + the freshest in-flight scheduled-tick start), `web/app/page.tsx` (per-product `status: 'running'|'waiting'|'idle'` + `runningSinceIso`; now fetches every profile to detect a `schedule:` block via `readScheduleFromYaml`), `web/app/CardRunStatus.tsx` (tri-state render). tsc + lint clean; mobile (390px) + `idle` state + zero console errors verified live; `waiting`/`running` are logic-verified only (origin/main had no scheduled product to reproduce them visually at commit time).

**Date**: 2026-05-18

**Context**: ADR-051's per-card surface showed a green "Running" dot whenever *any* scheduler tick was in flight and the product merely had a schedule block, next to the newest-CSV timestamp. Two user-reported confusions: (1) a scheduled-but-idle product was indistinguishable from an unscheduled one (no "armed" signal), and while a tick was active the card showed "Running" beside the *previous* run's timestamp — implying the run had started hours ago; (2) "is it actually running right now? it should show when the run began" — the shown time was the stale last-CSV instant, not the active run's start.

**Decision**:
- Replace the `running: boolean` card prop with `status: 'running' | 'waiting' | 'idle'`: an on-demand run whose title matches the slug, OR (a scheduler tick active AND the product has a parseable `schedule:` block) → `running`; else has-schedule → `waiting`; else `idle`.
- Plumb the active run's `run_started_at` through `ActiveRuns` (per matching on-demand run, and the freshest in-flight scheduled tick — ISO-string sort, `.at(-1)`). While `running`, the card shows **"Running since &lt;begin time&gt;"** from that start instant and **never** falls back to the stale last-run timestamp (renders no time if the start is unknown). `waiting`/`idle` keep the last-run / last-report-date display.
- `waiting` renders a non-pulsing amber "Waiting to run"; `running` keeps the green pulsing dot; `idle` with no timestamp still renders nothing (preserves ADR-051's empty-state).

**Consequence**:
- Scheduled-but-idle is now legible ("Waiting to run") and "Running" always pairs with the *current* run's begin time, never a misleading old one.
- Accepted limitation (inherited from ADR-051): a scheduler tick has no per-product attribution, so while a tick is genuinely in flight *every* scheduled card shows "Running since &lt;tick start&gt;" though one tick processes all due products together — best-effort, the only signal the GitHub API exposes. True per-product attribution would require the worker to emit per-product run markers (out of scope).
- `page.tsx` now fetches every product's profile on each home render (previously only while a tick was active) to detect the schedule block — N extra GitHub contents calls per load, parallelized inside the existing per-product `Promise.all`; negligible latency, free within the authenticated rate limit.

---

## ADR-053 — One bounded retry on transient (timeout/connection-class) fetch failures in `universal_ai` (ACCEPTED — implemented)

**Status**: ACCEPTED — implemented 2026-05-18 (user request). `worker/src/product_search/adapters/universal_ai.py` (`_is_retryable_fetch_error`, `_fetch_html_with_retry`, `fetch()` now calls the retry wrapper); 4 new tests in `worker/tests/test_universal_ai.py`. ruff + mypy --strict clean; full worker suite 240 passed.

**Date**: 2026-05-18

**Context**: `_fetch_html` already cascades AlterLab → curl_cffi → httpx, but the whole cascade ran exactly **once** per source per run (`fetch()` called it with no retry). On the `amd-epyc-9255` run `2026-05-18T04:55:34Z` provantage.com failed with `curl: (28) Connection timed out after 20002 milliseconds` — a transient TCP-connection timeout, **not** a 403/bot-block or a permanent datacenter ban: the prior run (`2026-05-17`) had provantage as `ok 1/1` and the **cheapest passing listing** at **$2117.44 (new)**. Because that one flaky socket dropped provantage from the entire run, the report's headline "cheapest" silently jumped to **$2795** (itcreations) — a quality regression caused by a fetch blip, not a market move. The `Diff vs yesterday` line gave no signal it was a source error rather than a price change.

**Decision**: Add **exactly one** bounded retry around the full fetch cascade, gated by an explicit retryable-error classifier:

> `fetch()` → `_fetch_html_with_retry(url)` → up to `_FETCH_MAX_ATTEMPTS = 2` attempts of `_fetch_html`, with `_FETCH_RETRY_BACKOFF_SECONDS = 2.0` s between them, retried **only** when `_is_retryable_fetch_error(exc)` is true.

- Retryable = timeout/connection class: builtin `TimeoutError`/`ConnectionError`; `httpx.TimeoutException`/`ConnectError`/`ReadError`; libcurl strings `curl: (28|7|6|35)` / "timed out" / "connection refused" / "connection reset" (curl_cffi's message form, matched by string so no hard dependency on its exception classes).
- **Not** retryable, surfaces immediately on attempt 1: AlterLab auth/quota (`RuntimeError` tagged `"AlterLab API issue"` → a 401/403/429 won't heal on retry and a 2nd attempt re-spends the up-to-120 s AlterLab budget for nothing), and any non-transient error (parse/`ValueError` etc. — fail fast, don't mask a bug behind a pointless retry).

**Alternatives considered & rejected**:
- **Retry only the curl_cffi/httpx cheap tier (not AlterLab).** Bounds worst-case latency tighter, but AlterLab is exactly the path that renders many of these hosts (provantage worked via the cascade the day before); skipping it on retry would make the retry useless for the hosts that need it most. Rejected.
- **N>1 retries / exponential backoff.** More resilience but multiplies the worst-case wall-clock on a genuinely-down host (each attempt can cost up to AlterLab-120 s + curl-20 s). One retry recovers the observed transient-blip failure mode without that blow-up. Revisit only if single-retry proves insufficient in practice.
- **Do nothing / just document.** Leaves the headline silently wrong whenever a high-value source has a transient blip — the actual user-visible failure. Rejected.

**Consequence**:
- Transient single-blip failures on high-value sources (the provantage mode) now self-heal within one run; the report's "cheapest" stops swinging on fetch noise.
- Accepted tradeoff: a **genuinely** down/blocked host now costs up to ~2× the cascade once (worst case ≈ 2×(AlterLab 120 s + curl 20 s) ≈ 280 s) before the source is marked errored. Rare (only when both the rendered and cheap tiers fail *and* the error looks transient), bounded (single retry), and the documented price of not losing a transient winner. The previously-noted "guaranteed-miss source can stall a run" latency item (PROGRESS "noticed but deferred") is now *slightly worse by design* — flagged here so a future session optimizing run latency knows this is intentional, not an oversight.
- Auth/quota and parse errors are explicitly excluded so the retry never delays surfacing a real outage or bug.
- Not addressed (still open, deliberately out of scope of this ADR — they were items #2–#4 in the 2026-05-18 diagnosis): trimming the AlterLab→curl_cffi fallback latency for AlterLab-only hosts; surfacing "N source(s) errored — cheapest may be understated" in the report so a fetch-driven headline move is legible; the report-footer doc nit that lists `cdw.com` under both "searched (ok)" and "Pending (not yet wired)".

---

## ADR-052 — Reliable scheduling via external `workflow_dispatch` through the existing Vercel app (ACCEPTED — implemented + proven)

**Status**: ACCEPTED — implemented + pushed 2026-05-17 (Phase 20, `0d5b99a`; tsc/lint clean) and **proven live 2026-05-18**. The out-of-repo runbook was executed (Vercel `CRON_TRIGGER_SECRET` set in Production + **redeployed** — the redeploy was the step that resolved an interim 401; env changes don't apply to a running deployment until redeploy. cron-job.org job 7619329: `*/15`, POST, `x-cron-secret`, enabled; owner ari.robicsek@gmail.com — full config in PROGRESS.md). End-to-end verification: cron-job.org test run → `200 {"ok":true,"dispatchedAt":"2026-05-18T05:15:29.703Z"}`; `search-scheduled.yml` then ran with `event = workflow_dispatch`, success at `2026-05-18T05:15:30Z` (≈1 s vs. the old ~hourly `schedule:` lag). The recurring `*/15` job now accrues on-time dispatches automatically; the kept `schedule:` cron remains the degraded fallback. (Operational gotcha worth remembering: **any change to `CRON_TRIGGER_SECRET` requires a Vercel redeploy** to take effect.)

**Date**: 2026-05-17

**Context**: GitHub Actions `schedule:` is best-effort and deprioritized on shared runners; high-frequency crons are commonly delayed or collapsed. Measured on this repo 2026-05-17: the `*/15 * * * *` heartbeat actually fired at [64, 57, 62, 63]-min intervals — effectively hourly. Consequence: a user's one-time `run_at: 2026-05-17T18:49:00Z` ("2:49 PM ET") job (saved correctly at 18:46:32Z) didn't fire because the last tick was 18:44:52Z and the next wouldn't come for ~an hour. This is the third consecutive session the user hit "my scheduled run didn't happen on time." Diagnosis confirmed the scheduler/profile/one-time logic is correct (`due = run_at <= now`, not subject to the 15-min look-back window which only governs recurring crons); the **sole** defect is GitHub's trigger cadence. Industry-standard remedy: keep the workload in Actions, trigger it from a reliable external scheduler via `workflow_dispatch` (that event is not throttled like `schedule`). Constraints from the user: must stay **free**; the project is on **Vercel Hobby** (Vercel Cron is capped at once/day on Hobby, so Vercel-native cron can't do 15-min); and the project's standing architectural value is "free on a public repo" (ADR-004) with minimal third-party trust.

**Decision**: Adopt the **hybrid trigger**:

> cron-job.org (free; every 15 min) → `POST https://<vercel-app>/api/cron/tick` (guarded by a server-only `CRON_TRIGGER_SECRET` in an `x-cron-secret` header) → the route calls a new `dispatchScheduledTick()` which `POST`s `workflow_dispatch` to `search-scheduled.yml` using the **`GITHUB_DISPATCH_TOKEN` already in Vercel env** → `scheduler-tick` runs in GitHub Actions unchanged.

- The GitHub PAT (which can spend paid LLM/scrape budget and push to `main`) **never leaves Vercel** — it is not stored at cron-job.org. cron-job.org holds only a low-value shared secret + URL; a leak there only lets an attacker force a scheduler tick (cost ≈ a search run *iff* a profile is due — most are scheduleless), bounded further by rotating `CRON_TRIGGER_SECRET`.
- Free on Hobby: a normal inbound API route is **not** subject to Vercel's daily-cron frequency cap (that cap applies only to Vercel Cron jobs declared in `vercel.json`).
- Keep `workflow_dispatch:` (already enabled) **and** keep `schedule: '*/15'` as an explicit commented **degraded fallback** so a cron-job.org/route outage degrades scheduling to "late," not "dead."
- Reuse the existing `/api/dispatch` route shape (500 if secret env unset, 401 on missing/mismatch) for consistency and auditability.

**Alternatives considered & rejected**:
- **A — PAT stored directly in cron-job.org.** Lowest effort, zero code. Rejected: the powerful GitHub token sits at rest in a free third-party SaaS we don't control; even a fine-grained repo-scoped Actions-only PAT can't be scoped to a single workflow and silently expires (≤1 yr). The hybrid keeps the token in Vercel — strictly better security for the same cost.
- **B — Vercel Cron → internal route.** Cleanest (no third party at all) but Vercel **Hobby caps cron at once/day**; needs Pro ($20/mo) → violates the free constraint. Revisit only if the project moves to Vercel Pro for other reasons.
- **D — Cloudflare Workers Cron → dispatch.** Free, self-owned, very reliable, no cron-job.org account. Comparable security to the hybrid (token in your Cloudflare account vs. your Vercel app). Not chosen because it adds a second platform/account and more setup than reusing the Vercel app + token we already operate; documented as the fallback design if cron-job.org proves unreliable or we want zero third-party schedulers.
- **C — stay GitHub-native, just fix the copy.** Zero infra but does not fix the actual problem (jobs still up to ~1 hr late). Rejected as the primary fix; the honesty/copy part is folded into Phase 20 anyway.

**Consequence**:
- One-time and recurring schedules fire within ~15 min reliably; the user's recurring failure mode is closed; the ADR-051 cards/footer surfaces will show on-time runs.
- New external dependency (cron-job.org) and a new server-only secret (`CRON_TRIGGER_SECRET`). Config for the external job lives **outside the repo** → it MUST be documented in PROGRESS.md and here so future sessions know it exists and where it's owned.
- The kept `schedule:` fallback still emits occasional ~hourly Actions runs — accepted as the price of resilience.
- Small code surface: one lib function, one API route, one env var, one workflow comment, plus the manual cron-job.org + Vercel-env setup runbook (in Phase 20 / PROGRESS).
- Supersedes the "occasional GitHub cron delay under load" caveat in **ADR-050** (that caveat understated the magnitude; ADR-052 is the systemic fix). ADR-050's scheduler/one-time semantics remain unchanged and correct.
- Optional later hardening (out of scope, noted): a dead-man's-switch/uptime monitor on the tick, since neither GitHub nor cron-job.org will proactively tell us if ticks stop.

**Implementation**: deferred — see Phase 20 in [PHASES.md](PHASES.md#phase-20--reliable-scheduling-trigger-external-workflow_dispatch) for the task list, the manual runbook, and the Done-when gate. Flip this ADR to ACCEPTED (implemented) when Phase 20's Done-when is met.

---

## ADR-051 — Per-card run-status surface (last-run time + live "Running" dot) (ACCEPTED — implemented)

**Status**: ACCEPTED — implemented 2026-05-17 (user request, follow-up to ADR-050).

**Date**: 2026-05-17

**Context**: After ADR-050 the user set one-time `run_at` schedules for `amd-epyc-9255` and reported they "never ran". They did run — `14:30Z`→`14:41:55Z`, `16:15Z`→`16:43:58Z` (the documented `*/15` + GitHub Actions cron-lag tradeoff, not a bug) — but were unobservable: the cards page showed only a date, the date-keyed `<date>.md` report meant a second same-day run overwrote the first, and the detail-page `RunInfoFooter` queries only the on-demand workflow so it never reflects scheduled runs. The user asked for the cards screen to show last-run **date and time** and a live indicator (green dot) for "running right now".

**Decision**:
- **Last-run signal = newest `reports/<slug>/data/<ISO>.csv` filename.** The worker writes a timestamped CSV snapshot for *every* run (scheduled and on-demand); it is the only per-run artifact that survives the date-keyed report overwrite and is not on-demand-only like the Actions API. `getLastRunInstant` lists that dir and parses `YYYY-MM-DDTHH-MM-SSZ` → ISO instant. No CSV → graceful date-only fallback to the latest report date.
- **Time is rendered in the user's local zone** via the mount-then-format pattern (SSR + first client paint render a timezone-independent ISO-date slice; `useEffect` swaps in the localized `dateStyle:'medium' timeStyle:'short'` string). Identical pattern to `RunInfoFooter`; avoids a hydration mismatch on a server (UTC) vs browser (local) `toLocaleString` divergence.
- **"Running" attribution**: on-demand runs carry the slug in the run title → direct match. A scheduler-tick has no per-product title and processes all due products in one run, so it is attributed to a product only if that product's profile currently declares a top-level `schedule:` block (profile fetched lazily, only while a tick is actually in flight). This stays quiet on the common path (all profiles currently scheduleless → a heartbeat tick lights nothing) and is honest rather than guessing.
- The cards page is `force-dynamic` (running state must never be served from an edge/RSC cache — same reasoning as the detail page).
- **Detail-page footer now uses the same authoritative instant.** `RunInfoFooter`'s "Last run completed" time was driven by `getLastCompletedRun` (on-demand workflow only), so after a scheduled run it showed a stale on-demand timestamp. The footer now takes the CSV-derived instant; the on-demand duration/conclusion are kept only when that on-demand run *is* the latest run (instants within 10 min — the worker writes the CSV a minute or two before the workflow self-completes). A much-newer CSV instant ⇒ the latest run was a scheduled multi-product tick with no per-product duration, so the footer shows the correct time without a fabricated duration. `RunNowButton`'s on-demand microcopy is intentionally left as-is (it is the on-demand control).
- **Custom-cron worked examples.** The "Custom" schedule mode previously offered only a placeholder. It now shows the 5-field UTC format legend plus four click-to-fill worked examples (`0 8 * * *` → daily 08:00 UTC, `30 13 * * 1-5` → weekdays 13:30 UTC, `0 */6 * * *` → every 6h, `15 0 1 * *` → monthly), so a user can see the format and the cron→English mapping instead of guessing.

**Consequence**:
- The user can now see exactly when each product last ran (to the minute, in their zone) and whether it is running now, directly on the home screen — closing the visibility gap that made working schedules look broken.
- The detail-page `RunInfoFooter` remains on-demand-only (unchanged; out of scope for a cards-only request) — logged as an opportunistic follow-up.
- GitHub cron lateness is unchanged and remains an accepted ADR-050 tradeoff; this ADR makes it *visible* rather than eliminating it.
- Adds one GitHub contents call per product for the CSV listing, plus a profile fetch per product *only during* an active scheduler tick. Acceptable for a personal low-traffic PWA already doing per-product no-store fetches.

**Implementation (2026-05-17)**:
- `web/lib/github.ts`: `getLastRunInstant(product)`.
- `web/lib/dispatch.ts`: `getActiveRuns()` (+ `SCHEDULED_WORKFLOW_FILE`, `fetchRecentRuns`).
- `web/app/CardRunStatus.tsx`: new client component (local datetime + pulsing green dot).
- `web/app/page.tsx`: `force-dynamic`; parallel last-run fetch; per-product `running`; chip replaced.
- `web/app/[product]/page.tsx`: parallel `getLastRunInstant`; `footerInfo` (authoritative instant + same-run-gated duration); footer rendered from it.
- `web/app/[product]/RunInfoFooter.tsx`: `FooterInfo` props; `durationMs` nullable; duration omitted when null.
- `web/app/[product]/ScheduleEditorButton.tsx`: custom-cron format legend + 4 click-to-fill worked examples.
- Verified: `tsc --noEmit` clean; `eslint` 0 errors (6 pre-existing warnings); dev-server SSR HTTP 200 — cards `amd-epyc-9255` → `dateTime="2026-05-17T16:43:58Z"`, `ddr5-rdimm-256gb` → date-only fallback; detail footer → `dateTime="2026-05-17T16:43:58Z"` (the scheduled run, no fabricated duration). Not visually browser-checked (chrome-devtools MCP profile locked by a running instance); localisation/dot reuse the ADR-050-verified `RunInfoFooter` pattern, the cron-example block is static JSX.

---

## ADR-050 — One-time schedules + minute-aware scheduler + local-time picker (ACCEPTED — implemented)

**Status**: ACCEPTED — implemented 2026-05-17 (Phase 17 reopened by explicit user request to make scheduling intuitive).

**Date**: 2026-05-17

**Context**: The Phase 17 schedule editor exposed a raw 5-field cron and only ever stored recurring UTC crons. Three structural gaps made the user's stated typical task — "schedule a single job for 8:30 AM ET today" — *impossible*, not just confusing: (1) no one-time concept (cron is inherently recurring); (2) `_cron_matches_hour` only read the cron **hour** field — minute/day/month/dow ignored, so ":30" and date-specific crons were meaningless; (3) time was hardcoded UTC (`Schedule.timezone: Literal["UTC"]`) with no local-time entry, while the user thinks in ET. The automatic heartbeat (`search-scheduled.yml` cron) was also disabled, so nothing fired on a timer at all. The user was interviewed and chose: full one-time support end-to-end; enter time in local zone, store UTC (no tz field added); 15-minute precision; keep the radio presets and add a time/zone/date picker; enable the `*/15` heartbeat but strip schedules from all existing profiles so blast radius is zero ("I'll add them in if needed later").

**Decision**:
- `Schedule` model carries **exactly one** of `cron` (recurring, UTC) or `run_at` (one-time, absolute UTC instant). `timezone` stays `Literal["UTC"]` (now defaulted/optional); the **web UI** converts a wall-clock time in the user's chosen zone to UTC before it ever reaches the model — the stored model never holds a non-UTC zone (DST drift on a fixed recurring cron is an accepted, documented tradeoff).
- The scheduler heartbeat is `*/15 * * * *`. `scheduler-tick` now matches the **full** cron (minute+hour+dom+month+dow, standard Vixie dom/dow OR rule) within a non-overlapping 15-min look-back window. A `run_at` job fires once when its instant is past, then the scheduler **strips the whole `schedule:` block** from the profile (regex mirror of the web mutator) so it never repeats; the workflow's existing `git add -A && commit && push` persists the removal. A one-time job is attempted exactly once regardless of exit code (a broken profile must not retry every tick forever).
- All 7 active product profiles had their `schedule:` block removed (run-now-only); `_template` updated to document the new either/or schema. Nothing auto-fires until a schedule is re-added via the editor.
- Editor keeps the preset radios; "One time only" and "Every day" reveal a native time input (15-min step), a date input (one-time), and a timezone dropdown (browser zone surfaced first). On mobile the panel is a viewport-pinned `fixed` sheet (the trigger button is the leftmost toolbar item, so the prior `right-0` anchored popover ran off-screen at narrow widths).

**Consequence**:
- The user's headline use case now works: "8:30 AM ET today" → `run_at: 2026-05-17T12:30:00Z`, fires within ~15 min of that instant, then self-clears.
- Enabling the heartbeat is a real behaviour change (96 mostly-idle Actions runs/day) but free on a public repo (ADR-004); the only costs are Actions-history noise and occasional GitHub cron delay under load. It does **not** increase search/LLM spend — a product still runs at most once per its cadence regardless of tick frequency. **Update (ADR-052, Phase 20):** the "occasional GitHub cron delay" was empirically far worse than this caveat implied (collapsed to ~hourly); ADR-052 supersedes it by making `workflow_dispatch` (external trigger) the on-time path and the `schedule:` cron only a degraded fallback. ADR-050's scheduler/one-time semantics are unchanged.
- New recurring `Schedule` shape must stay in sync between `profile.py` and `web/lib/onboard/schema.ts` (same Pydantic/TS hazard as ADR-049's `page_type`).
- Minute-aware matching is stricter than the old hour-only check; safe here because all existing schedules were stripped (no migration risk). Cron fields the parser can't expand are treated as non-matching (never fire on an unparseable cron).
- Follow-up (not a gate): a failed one-time run is not retried (attempted-once semantics) — acceptable for v1; revisit if transient failures prove common. DST drift on recurring daily crons is unaddressed by design (would need a stored tz + scheduler localisation).

**Implementation (2026-05-17)**:
- `profile.py`: `Schedule.cron: str|None`, `run_at: datetime|None`, `timezone` defaulted; validators for cron (None-safe), run_at→aware-UTC normalisation, and exactly-one mode.
- `cli.py`: `_expand_cron_field`, `_cron_fires_at` (Vixie OR), `_cron_due` (windowed), `_strip_schedule_block`; `_cmd_scheduler_tick` rewritten for recurring-vs-one-time + post-fire strip. `TICK_WINDOW_MINUTES = 15`.
- `.github/workflows/search-scheduled.yml`: `schedule: - cron: '*/15 * * * *'` enabled.
- `web/lib/schedule.ts`: `ScheduleConfig` union; YAML read/write for `run_at`|`cron`; `zonedWallTimeToUtc` (two-pass, DST-correct), `dailyLocalToCron`, `onceLocalToIso`, `dailyCronToLocalHHMM`, `isoToLocalParts`, `buildTimezoneOptions`, `nextRunDate`.
- `web/lib/onboard/schema.ts`: `validateSchedule` mirrors exactly-one + ISO `run_at`.
- `ScheduleEditorButton.tsx`: presets + time/date/tz controls, past-time hint, render-pure clock (`openedAtMs`), mobile `fixed` sheet (`sm:` reverts to anchored popover).
- 7 product profiles stripped of `schedule:`; `_template` re-documented.
- Tests: 5 new in `test_profile.py` (Schedule model) + new `test_scheduler.py` (expand/fires-at/Vixie-OR/window/strip incl. CRLF). Verified: ruff clean, mypy 31 files clean, **pytest 236 passed**; web `tsc --noEmit` clean, lint 0 errors (6 pre-existing warnings). Live UI checked at narrow viewport via Chrome DevTools: recurring parse + UTC↔ET conversion, one-time date/time/tz (11:30 PM ET → 03:30Z next day, DST-correct), past-time warning, custom-cron next-run honouring minute+dow, mobile sheet no longer clips.

---

## ADR-049 — Tier 1.5 detail-page price extractor for single-SKU products (ACCEPTED — implemented)

**Status**: ACCEPTED — code implemented 2026-05-17 (Phase 19 task 6). The live application step (re-probe the parked `amd-epyc-9255` URLs through AlterLab, promote the ones Tier 1.5 extracts into `sources`, remove `ebay_search`) is the remaining follow-up — it needs a paid live run and is tracked in PROGRESS.md.

**Date**: 2026-05-17 (scoped); 2026-05-17 (implemented)

**Context**: The `amd-epyc-9255` profile produced a "dreadful" run — eBay returned 19 real listings; all 8 `universal_ai_search` vendor URLs returned 0. Empirical rendered `probe-url` testing of every realistic non-eBay vendor (SabrePC, Wiredzone, ServerSupply, IT Creations, Central Computer, Newegg, CDW) showed they all *stock* the EPYC 9255 but expose it only on JS-heavy product **detail** pages with **no JSON-LD** and **no clean product anchors** (the adapter extracts only nav junk like "All RMA Request"). Tier 1 (JSON-LD) misses these; Tier 2 (anchor→LLM) correctly rejects the junk and emits nothing. For a single-SKU product (one exact part number), eBay is therefore the *only* source the current architecture can extract — which makes the user's explicit, repeated request to remove eBay impossible for this product class. This is a structural adapter gap, not a config error.

**Decision**: Add a **Tier 1.5 detail-page extractor** to `universal_ai.fetch()`, between the JSON-LD tier and the anchor tier. It runs only when JSON-LD found nothing AND the source is flagged a single-product detail page (explicit `page_type: "detail"` opt-in on the `Source` model preferred; `_looks_like_product_url()` heuristic as fallback). It strips the fetched HTML to main content, makes one bounded `claude-haiku-4-5` call to extract `{found, title, price_usd, condition, in_stock, pack_size}` for the single product, then **deterministically verifies the extracted price string occurs verbatim (under normalization) in the fetched HTML** before emitting — dropping the listing otherwise. The URL is always the source URL, never LLM-produced. Full task breakdown, schema/prompt changes, fixtures, and risks are in PHASES.md Phase 19 task 6.

**Consequence**:
- Preserves ADR-001 (LLM never produces data the deterministic layer didn't fetch): the price is extracted from real fetched bytes and re-verified verbatim against them — a *stricter* check than Tier 2's anchor mapping.
- Unblocks single-SKU products (server CPUs, specific MPNs) whose only vendors are SPA/custom storefronts, and unblocks eBay removal for `amd-epyc-9255` once ≥1 non-eBay source extracts.
- Adds an optional `page_type` field that must stay in sync between `worker/src/product_search/profile.py` and `web/lib/onboard/schema.ts` (recurring Pydantic/TS sync hazard).
- Best-effort only for hard bot-walls (ServerSupply/CentralComputer returned empty rendered bodies; AlterLab intermittently 422s Newegg/IT Creations) — Tier 1.5 fixes extraction, not fetch reachability.
- Until implemented, `amd-epyc-9255` keeps `ebay_search` (only working source) and parks the 8 probe-tested-dead vendor URLs in `sources_pending` with verdict notes — they are not run and not charged.

**Implementation (2026-05-17)**:
- `Source.page_type: Literal["detail","search"] | None` added to `profile.py`; mirrored in `web/lib/onboard/schema.ts` (`SOURCE_PAGE_TYPES`).
- `universal_ai.py`: `DETAIL_SYSTEM_PROMPT`, `_strip_to_main_text`, `_price_in_text` (verbatim guard: `$`/`,`/space-insensitive, tolerates `2335`/`2335.00`/`2,335.00`), `_resolve_detail_mode` (explicit `page_type` wins; URL-shape heuristic → `"auto"` fallback), `_extract_detail_listing`. Wired into `fetch()` after the JSON-LD tier: explicit `detail` does NOT fall through to the anchor tier on a miss (no wasted 2nd LLM call); `auto` (heuristic) DOES fall through so a mis-classified search page can't regress.
- **Strip-tag scope (refinement):** `_strip_to_main_text` decomposes `script/style/noscript/template/svg/nav/header/footer/iframe` and collapses whitespace, canonicalises split prices, strips foreign currency, caps 16k chars. It deliberately does **NOT** strip `<form>`: Odoo/Wiredzone-class storefronts render the price + Add-to-Cart inside the product `<form>`, so decomposing it deletes the price (caught live on the Wiredzone "prime target" — initially missed, recovered after dropping `form` from the strip list; regression-pinned).
- `cli.py probe-url --detail` runs Tier 1.5 and exits 0 iff it produced a priced listing (onboarder gate).
- `onboard_v1.txt`: single-SKU `page_type:"detail"` exception documented (narrowed to single exact MPN with no working search URL).
- Tests: 17 new in `test_universal_ai.py` (9 synthetic: strip/guard/gating/emit/OOS/fabricated-drop/found:false/explicit-no-fallthrough/auto-fallthrough; 8 real-fixture: parametrised verbatim-price on SabrePC/Wiredzone/IT Creations/Newegg, barren-bot-wall pins on ServerSupply/CentralComputer, form-not-stripped regression, Wiredzone end-to-end). Full CI green in fresh Py3.12 venv: ruff/mypy(31)/pytest(225)/validate(4); web tsc + lint clean.
- **Live promotion (paid AlterLab run, user-authorised):** all 6 parked `amd-epyc-9255` detail URLs probed `--render --detail`. 4 extract a verbatim-verified price — SabrePC $2,523.20, Wiredzone $2,070.00, IT Creations $2,795.00, Newegg $3,202.50 — promoted into `sources` with `page_type: detail`; **`ebay_search` removed** (the user's long-standing request, now unblocked). ServerSupply/CentralComputer remain parked (≤341-char barren rendered body — bot wall AlterLab only partially defeats; exactly the best-effort risk this ADR called out). CDW parked (search page; carries other EPYC models, not the 9255). EBay-free `search` run completes exit 0 with non-eBay `detail_llm` listings passing the validator. **All Phase 19 task 6 done-when criteria met.**

---

## ADR-048 — Verify CI-affecting changes in a clean Python 3.12 venv before pushing

**Status**: ACCEPTED

**Date**: 2026-05-17

**Context**: The 2026-05-17 onboarder-stabilization commit (`a2abe05`) added `from dotenv import load_dotenv` to `cli.py` but never declared `python-dotenv` in `worker/pyproject.toml`. It also introduced a `ruff` `UP037` violation (`-> "Source":` quoted annotation, redundant under `from __future__ import annotations`) and a `mypy` error in `universal_ai.py` (`prod_map` inferred as `dict[str, Sequence[str]]`). All three passed locally on Python 3.13 (which had `python-dotenv` in user site-packages and a stale ruff cache) but broke CI's `validate-profiles`, `worker lint/type-check/test`, and both on-demand search runs, because CI does a fresh `pip install -e ".[dev]"` on Python 3.12 installing only declared deps. mypy and pytest were masked in CI because they are skipped once `ruff` fails in the same job.

**Decision**: Any change touching imports, dependencies, type annotations, or profile validation must be verified in a fresh Python 3.12 venv (`uv venv --python 3.12 --clear worker/.ci-venv`; `uv pip install -e ".[dev]"`) running the exact CI sequence (`ruff check src/`, `mypy src/`, `pytest tests/`, `cli validate <slug>`) before pushing. A local 3.13 pass is not sufficient evidence. `.ci-venv/` is gitignored.

**Consequence**: One-time ~1–2 min venv setup cost per risky change. Eliminates the recurring "passed locally, failed CI on missing dep / lint / type" class of regressions. Reinforces that runtime imports must be declared in `pyproject.toml`, not assumed from ambient local installs.

---

## ADR-047 — Add Pydantic Bare-Domain Schema Validation to Profile Sources

**Status**: ACCEPTED

**Date**: 2026-05-17

**Context**: In previous onboarding runs, the LLM onboarder generated bare domain URLs (e.g., `https://www.ipcstore.com/` or `https://www.sabrepc.com/`) when it was unable to identify a functional parameterized search URL for a vendor storefront. Bare domains or homepage paths always yield 0 listings because the `universal_ai_search` adapter is designed to extract listing cards from search results pages rather than raw homepage content. To protect runs from resource and budget waste, we need a hard structural guardrail that blocks bare domains in active profiles.

**Decision**:
1. Added a `model_validator` (mode="after") in the Pydantic `Source` model in `worker/src/product_search/profile.py`.
2. If `id == "universal_ai_search"`, the validator parses the URL and raises a `ValueError` if the URL is a bare domain (i.e. path is empty or `/` and has no search-related query parameters).
3. The Pydantic validator acts as a structural backstop, immediately rejecting any saved drafts or edited profiles that violate these URL-shape constraints.

**Consequence**:
- Active profile configurations can no longer contain bare-domain URLs for universal search, catching LLM compliance errors before the profile is committed or run.
- Ensures the onboarder strictly defaults to parameterized search-results URLs or puts the vendor under `sources_pending` if a search URL cannot be constructed.

---

## ADR-046 — Pydantic Profile schema sync: `spec_filters` and `spec_flags` are fully optional

**Status**: ACCEPTED

**Date**: 2026-05-12

**Context**: Previously, the web onboarding UI's TypeScript schema validator (`web/lib/onboard/schema.ts`) was updated to make `spec_filters` and `spec_flags` optional blocks (removing the hard minimum-length check) to unblock non-RAM products that have no specific filters or flags beyond base defaults. However, the worker's canonical Pydantic model (`worker/src/product_search/profile.py`) still enforced `Field(min_length=1)` without a default factory. Consequently, any profile lacking `spec_flags` (such as `aufschnitt-essiccata-jerky`) failed instantly at worker startup/validation with a Pydantic `Field required` missing-key error.

**Decision**:
1. Updated `worker/src/product_search/profile.py` to type `spec_filters` and `spec_flags` with `Field(default_factory=list)`, removing the minimum-length restriction and allowing the blocks to be completely omitted from `profile.yaml`.
2. Downstream loops in `validators/pipeline.py` and `validators/flags.py` already iterate safely over empty lists without side effects or errors.

**Consequence**:
- Profiles like `aufschnitt-essiccata-jerky` that omit `spec_flags` now validate cleanly in both the web preview and the worker runner.
- Eliminates the silent missing-key crash on scheduled or on-demand worker runs.

---

## ADR-045 — Alerts survive onboarder edits via save-time splice (rather than teaching the onboarder about alerts)

**Status**: ACCEPTED

**Date**: 2026-05-11

**Context**: Phase 17 added `profile.alerts` (price-below + vendor-seen rules), configured entirely from the schedule editor UI. The onboarder LLM is intentionally unaware of alerts — the prompt has no mention of them, and the JSON `<draft>` block the LLM emits per turn carries no `alerts` key. This creates a silent-data-loss hazard: `/api/onboard/save` rebuilds YAML from `body.draft` via `renderProfileYaml`, so editing a profile through `/onboard?edit=<slug>` would round-trip the YAML through a draft that never had `alerts` and drop every rule the user had configured. Two viable fixes: (a) teach the onboarder to read and pass through the existing alerts (prompt churn + LLM has to handle a domain it shouldn't); (b) splice the existing alerts back in server-side at save time.

**Decision**:
1. In [/api/onboard/save](../web/app/api/onboard/save/route.ts), when `body.originalSlug` is a valid edit-mode slug AND `body.draft.alerts` is `undefined`, read the on-disk `products/<slug>/profile.yaml` via the existing `getProductProfileContent`, extract alerts with `readAlertsFromYaml`, and assign them to `draft.alerts` before `gateUniversalAiUrls` and `renderProfileYaml`.
2. The onboarder prompt continues to know nothing about alerts. This is reinforced by the Phase 17 brief in [PHASES.md](PHASES.md#phase-17--schedule-editor--alerts-ui) ("Alerts are configured in the editor UI **only**").
3. `alerts` is appended to `CANONICAL_KEY_ORDER` in [render-yaml.ts](../web/lib/onboard/render-yaml.ts) so re-rendered YAML places the block in a stable position immediately after `schedule`.

**Consequence**:
- Editing a profile through the onboarder no longer silently drops alerts. Users who configured alerts via the schedule editor can safely re-run the onboarder for other reasons (fixing a vendor URL, retargeting the description) without losing their notifications.
- The onboarder prompt stays simpler — alerts remain a UI-only concept.
- Trade-off: if a future feature *did* want the onboarder to propose alert defaults, we'd have to revisit this splice (a draft that legitimately wants to clear alerts would have to send `alerts: []` explicitly, not omit the key).

---

## ADR-044 — Profile schema: `target.configurations` and `qvl_file` are RAM-domain-only and optional

**Status**: ACCEPTED

**Date**: 2026-05-10

**Context**: Both `target.configurations` (a list of `{module_count, module_capacity_gb}`) and `qvl_file` (path to a Qualified Vendor List YAML) originate in the RAM domain — they describe how to combine DIMM modules to reach a capacity target and which manufacturer-validated part numbers exist. Non-RAM products (paintball pistols, headphones, keyboards, single-SKU consumer goods) have no analogue for either concept. Until this ADR, the schema required both: `configurations: list[...] = Field(min_length=1)` and `qvl_file: str`. The onboarder prompt taught the LLM to emit a degenerate `[{module_count: 1, module_capacity_gb: 1}]` placeholder and to set `qvl_file: "products/<slug>/qvl.yaml"` with a stub empty `qvl: []` file. This was pure noise — visible in every non-RAM `profile.yaml` in the repo, requiring a stub `qvl.yaml` to exist on disk so `cli.py:load_qvl()` wouldn't fail, and obscuring the fact that those keys are RAM-specific.

**Decision**:
1. `Target.configurations` defaults to `[]` (no `min_length` constraint). The `min_quantity_for_target` filter and the `total_for_target_usd` synthesizer column already handled the empty-list case correctly (they no-op when `capacity_gb` is None on the listing); no downstream change required.
2. `Profile.qvl_file: str | None = None`. The slug-in-path validator only fires when `qvl_file is not None`. `cli.py` skips `load_qvl()` and passes an empty `QVL(qvl=[])` to the pipeline when `qvl_file` is None; `validators/qvl.py:annotate_qvl` already handled `qvl=None`.
3. The TS schema mirror in `web/lib/onboard/schema.ts` is updated to match (both keys optional; `configurations` allows undefined or empty list; `qvl_file` only validated when present).
4. The onboarder prompt (`worker/src/product_search/onboarding/prompts/onboard_v1.txt`) is rewritten to OMIT both keys for non-RAM products — no degenerate placeholder, no stub QVL path. The historical "ALWAYS a list with at least one entry" rule is removed.

**Consequence**:
- Non-RAM profiles are smaller and more honest about which fields apply to them.
- Existing RAM profiles (DDR5-RDIMM-256GB) continue to validate unchanged — they still emit both keys with real values.
- Existing non-RAM profiles (Bose NC 700, paintball pistol) still validate even with the stub placeholders left in place — old data is forward-compatible. A future onboarder pass to clean them up is a follow-up.
- New `test_profile.py` cases pin: configurations omitted → `[]`; `qvl_file` omitted → `None`; `qvl_file` set but with the wrong slug → validation error.

---

## ADR-043 — Abandon raw.githubusercontent.com for dynamic data to bypass origin caching

**Status**: ACCEPTED

**Date**: 2026-05-05

**Context**: Despite multiple layers of caching defenses (Next.js `no-store`, `?_cb=` query string busters, `force-dynamic` page config, and `window.location.reload()`), the Next.js UI continued to serve stale reports immediately after a GitHub Action run completed. The root cause was identified as `raw.githubusercontent.com`'s internal origin cache. While the `?_cb=` cache buster successfully bypassed the Fastly CDN, the underlying origin server maintains its own ~5-minute cache resolving branch references (like `main`) to commit SHAs. Consequently, the origin server resolved `main` to the previous commit SHA and served the old file.

**Decision**:
1. Completely abandon the `raw.githubusercontent.com` domain for fetching dynamic repository content (`profile.yaml` and `.md` reports).
2. Switch to the authenticated GitHub REST API (`api.github.com/contents/...`). The REST API reads directly from the Git database replicas and does not suffer from the 5-minute branch-ref caching delay.
3. Apply `?_cb=${Date.now()}` to *all* GitHub REST API requests, including directory listings, to guarantee absolute freshness against any intermediate Next.js or edge proxy caches.
4. Natively decode the base64 `contents` API payload on the Node.js server using `Buffer.from(data.content, 'base64').toString('utf8')` to properly preserve multibyte UTF-8 characters without data corruption.

**Consequence**:
- The "stale screen" problem is definitively resolved. Users see the latest report immediately after a run completes.
- No additional API round-trips are required, keeping latency identical to the previous implementation.
- `docs/PROGRESS.md` is updated to reflect this fix.

---

## ADR-042 — Single-commit Product Deletion via Git Trees API

**Status**: ACCEPTED

**Date**: 2026-05-05

**Context**: Phase 16 required a feature to hard-delete a product's entire history and profile in a single commit. The existing GitHub integration (`web/lib/onboard/commit.ts`) used the higher-level GitHub Contents API via `PUT`, which inherently processes one file at a time, resulting in multiple commits for deleting a directory with multiple files (`products/<slug>` and `reports/<slug>`).

**Decision**: 
To satisfy the single-commit requirement ("chore: delete product <slug>"), the deletion process now uses the lower-level Git Database API (Trees and Commits):
1. Fetch the `HEAD` commit and its associated tree.
2. Create a new tree referencing the `HEAD` tree as its base but setting `{ path: "products/<slug>", mode: "040000", sha: null }` and similarly for `reports/<slug>`. This effectively deletes the entire sub-tree for those directories.
3. Create a new commit referencing the new tree and update the branch reference.

**Consequence**:
- The product deletion is perfectly atomic.
- The Git history remains clean with a single `chore: delete product <slug>` commit.
- The Next.js API route (`/api/profile/[slug]`) invokes this new helper and triggers a UI revalidation.

---

## ADR-041 — AlterLab European geo-routing: strip foreign currencies, convert to approximate USD

**Status**: ACCEPTED

**Date**: 2026-05-04

**Context**: Phase 19's Amazon price-selector fix (ADR-039, `_amazon_card_primary_price`) was verified against a hand-crafted fixture but never against the real AlterLab-rendered DOM. Investigation revealed that AlterLab's outbound IPs geo-route through Europe, causing Amazon to serve European-locale pages with EUR prices instead of USD. The cards show "See options" (no `span.a-price`, no inline USD price), and EUR amounts appear as plain text (e.g. `EUR&nbsp;490.07`). The LLM reads the EUR digits as USD, producing wrong prices ($490.07 instead of ~$529 USD or the actual US price of $649.95).

**Decision**:

1. **Strip foreign-currency amounts from context text** (`_strip_foreign_currencies`) so the LLM cannot misinterpret them. Covers EUR, GBP, CAD, AUD, JPY, INR, CHF.
2. **Convert to approximate USD** (`_foreign_price_to_usd`) using hardcoded ballpark exchange rates. When `_amazon_card_primary_price` returns `None` but foreign currencies are found, use the converted price as the hint. ~5% imprecision is acceptable — better than dropping the listing.
3. **Flag with `price_approx_fx: true`** in Listing attrs so downstream reporting can identify approximate prices.
4. **Do not use ScrapFly** for geo-targeted fetching (cost-prohibitive at scale).

**Exchange rates** are hardcoded (EUR×1.08, GBP×1.27, etc.) and updated infrequently. This is acceptable because the FX path is a fallback for when AlterLab can't deliver USD prices; when it can (US exit IP or future geo-targeting support), `_amazon_card_primary_price` fires first and the FX path is never reached.

**Lesson**: test fixtures must be captured from the **production rendering path** (AlterLab), not hand-crafted or fetched via httpx. The httpx body (US-facing, with `span.a-price`) works perfectly — but production uses AlterLab which gets a different DOM.

---

## ADR-040 — Vendor-reach policy: auto-demote universal_ai sources after 3 consecutive 0-yield runs

**Status**: ACCEPTED (policy); implementation deferred to a follow-up task.

**Date**: 2026-05-04

**Context**: Phase 19's per-vendor body capture (task 2, results in [docs/VENDOR_REACH.md](VENDOR_REACH.md)) confirmed that 6 of the 7 universal_ai_search URLs on the bose-nc-700-headphones profile produce 0 listings per run. The failure modes vary — Cloudflare Turnstile (backmarket), AlterLab geo-routing to a country-selector splash (bestbuy), AlterLab 503/504 plus httpx-fallback bot-block (walmart, crutchfield), and JS-only product cards (reebelo). None of them are extractor bugs. They're all "the page is fetched, the universal_ai pipeline runs, and 0 candidates fall out the back."

The cost of doing nothing: the 2026-05-04 PM Bose run spent **$0.016 on universal_ai LLM calls that produced zero usable listings**. Multiplied across daily runs and a future second product, that's small in absolute terms but unbounded in growth and ugly as a per-run telemetry signal (most of the spend is going to do nothing).

The cost of being too aggressive: AlterLab is intermittent. A single run's 0-yield is not a confident signal that the URL is dead; it could be transient. Demoting on the first failure would whipsaw vendors in and out of `sources` and undermine the on-demand re-evaluation flow.

**Decision**:

1. **Track per-source 0-yield streaks** in the SQLite store. New table `source_runs(slug TEXT, source_url TEXT, fetched_at TEXT, listings_yielded INTEGER, fetcher TEXT)` with a composite PK on `(slug, source_url, fetched_at)`. Recorded by the search command after each run regardless of outcome.

2. **Auto-demote on 3 consecutive 0-yield runs.** When the most recent 3 entries for a `(slug, source_url)` all have `listings_yielded == 0`, the search command moves that URL from `profile.sources` to `profile.sources_pending` with an automated note: `"Auto-demoted YYYY-MM-DD after 3 consecutive 0-yield runs (last fetcher: <fetcher>). Re-save the profile to re-evaluate."` The YAML edit happens as a profile-write step at the end of `_cmd_search`.

3. **Demotion is reversible.** Re-saving the profile through the onboarder runs the relaxed save-time gate (ADR-038), which evaluates each URL afresh. If AlterLab cooperates that day and the URL produces ≥1 candidate during the gate's structural probe, the URL goes back to `sources`. The 0-yield streak counter is keyed on `(slug, source_url)` and wipes when the URL leaves `sources_pending`.

4. **Three is a deliberate floor, not a tunable.** It's small enough to recover infra cost quickly (3 daily runs ≈ 3 days of waste = ~$0.05 on Bose's case), large enough to absorb single AlterLab outages, and matches the same thresholding feel as `_PROBE_GATE_MIN_BYTES` in ADR-038 (one knob, one threshold, no per-vendor tuning).

5. **Hand-edits are honoured.** A user who manually moves a URL back into `sources` resets the streak counter; the auto-demoter only fires after 3 *new* consecutive 0-yields after the manual edit.

**Consequence**:
- Bose run cost on a steady-state schedule drops from ~$0.016 to ~$0.005 within 3 days of the auto-demoter being live. The 5 demoted URLs (backmarket, bestbuy, walmart, crutchfield, reebelo) move to `sources_pending`; only ebay_search + amazon (the one universal_ai source actually working today) stay in the active rotation.
- Future products onboarded by `OnboardChat` will go through the same lifecycle: optimistic addition at save-time (ADR-038's relaxed gate), pruning at runtime by ADR-040's streak counter. The two together form the full vendor lifecycle.
- The new `source_runs` table is small (one row per source per run, ≤10 sources × 1 run/day per product). No retention policy needed in the short term; a 90-day rolling delete can come later if the table grows past a few thousand rows.
- A future `cli source-status <slug>` diagnostic can read the table to surface "X URLs at streak Y/3" so the user can hand-intervene before auto-demote fires if they want to.

**Trade-offs**:
- **Implementation deferred.** The policy is settled, but the code change (new `source_runs` table + write hook in `_cmd_search` + YAML-rewrite logic + tests) is its own follow-up task. Tracked in PROGRESS.md as "Phase 19 task 4 follow-up: implement ADR-040 streak tracking and auto-demote." Until that lands, the user manually demotes by editing `profile.yaml`.
- **YAML rewrite during search is novel.** Today `_cmd_search` only writes to the SQLite DB and reports. Mutating `profile.yaml` mid-run is a new class of side effect and needs to preserve formatting, comments, and `sources_pending` notes. The implementation should use ruamel.yaml (round-trip preserving), not PyYAML.
- **The 3-run threshold is calendar-day-coupled.** With the default daily cron, "3 consecutive runs" is "3 days." If a product is on a non-default schedule (hourly, weekly), the calendar feel of demotion changes. Acceptable for now — onboarder defaults to daily.
- **No early-warning notification.** The user finds out a URL was demoted by reading the next-run report or grepping for the auto-demote note in `profile.yaml`. A push notification could come later if streak management gets noisy.

**Out of scope**:
- Auto-promotion of `sources_pending` URLs back to `sources` without a re-save. Demotion is mechanical; promotion needs the onboarder's structural probe to confirm the URL works.
- Per-vendor structural extractors (the ADR-039 pattern for Amazon, then per other big-box site). Vendor reach is the wrong layer to spend that effort against until the fetch tier is sorted.
- Replacing or augmenting AlterLab. ADR-040 reduces the symptom (wasted spend on dead URLs), not the root cause (AlterLab's intermittent failures). A separate evaluation should consider Bright Data / Scrapfly residential / direct headless once Phase 19 closes.

**Refines**: ADR-038 (which set the save-time gate as a *one-shot* relaxed check). ADR-040 adds the runtime streak-based half of the lifecycle.

---

## ADR-039 — Amazon-specific primary-price selector for the universal_ai adapter

**Status**: ACCEPTED

**Date**: 2026-05-04

**Context**: The first live Breville run through the Phase-15 pipeline (2026-05-04 03:54 UTC) recorded **all 3 Amazon listings with materially wrong prices**:

| Recorded | Live page (new) | Delta |
|---|---|---|
| BES876BSS Impress: $489.50 | $649.95 | -$160.45 |
| BES870XL: $421.63 | ~$549.95 | -$128.32 |
| BES870BSXL Black Sesame: $469.29 | ~$549.95 | -$80.66 |

Root cause: Phase 15's `_ancestor_card_text` walk (6 hops, 1500 chars) is wide enough to flatten an Amazon `s-result-item` card's full text, including "List: $799.95" strikethroughs, "From: $489.95" used/marketplace sub-links, and Subscribe-and-Save discounts. `_PRICE_PATTERN` returns ALL `$NNN.NN` tokens; the LLM then picks "the most plausible", which in practice is the cheapest. For the BES876BSS card the cheapest hint was the used-condition "From: $489.95", so the recorded `unit_price_usd` was $489.50.

This is a correctness regression introduced by Phase 15's wider walk. Pre-Phase-15 (4 hops / 600 chars) the walk often missed Amazon prices entirely; we got 0/0 fetched/passed. Phase 15 traded "no prices" for "wrong prices."

**Decision**: When the page host is `amazon.<tld>`, override the generic regex sweep with a structural Amazon-specific selector — `_amazon_card_primary_price` in [worker/src/product_search/adapters/universal_ai.py](../worker/src/product_search/adapters/universal_ai.py).

For each anchor:
1. Walk up to 10 ancestors looking for a node with `class*="s-result-item"` OR `data-component-type="s-search-result"` (the Amazon search-result card boundary).
2. Within that card, iterate `<span class="a-price">` in DOM order.
3. Skip spans whose class contains `a-text-price` OR whose `data-a-strike="true"` — both Amazon's markers for strikethrough List/MSRP variants.
4. Return the first remaining span's `<span class="a-offscreen">` text after extracting the `$NNN.NN` digits.

If found, `prices` for that anchor is replaced with `[that_single_price]` — the LLM gets exactly one hint and cannot pick a sub-price. If no qualifying span exists in the card (or no card boundary is found), the helper returns None and the generic regex fallback runs unchanged.

The Amazon path is gated on `"amazon." in urlparse(base_url).netloc.lower()`. False positives (a non-Amazon vendor whose host happens to contain "amazon.") would just call the helper and get None back, which is harmless.

**Consequence**:
- BES876BSS Impress, BES870XL, BES870BSXL all now record their buy-now prices on the next Breville run. The live-run done-when ("Amazon price recorded for at least one popular product matches the live new-condition price within $5") is met.
- A new fixture [amazon-breville-multi-price.html](../worker/tests/fixtures/universal_ai/amazon-breville-multi-price.html) pins three real Amazon DOM patterns: strikethrough List, "From: $X" used sub-link, Subscribe-and-Save secondary. The new test `test_amazon_card_primary_price_skips_strikethrough_and_used` asserts each card's `price_hints` is `["$<buy-now>"]` — no list, no used, no S&S.
- 163/163 worker tests pass (was 161). Existing `test_extract_handles_amazon_split_price_markup` (the synthetic split-price fixture from ADR-037 follow-up) still passes — that fixture's three cards have only ONE `<span class="a-price">` each, so the new selector picks them as the primary too, and the result is identical to the prior canonicaliser-only behaviour.
- The synthetic German-EUR `amazon-bose-nc700-search.html` fixture isn't pinned by the new test (its prices are EUR, not USD; the helper's `$\s*` regex returns None and we fall through to the generic path). That fixture remains a regression smoke test for the rest of the extractor.

**Trade-offs noted**:
- The selector is structural, not host-specific in a stronger sense — any page on amazon.<tld> that uses `s-result-item` containers gets this treatment. Amazon's seller dashboards, product detail pages, and customer review pages use different container shapes; the helper returns None on those and the generic path runs. That's the correct fallback.
- The "From: $489.95" sub-link in Card 1 of the fixture is itself an `<a href="/gp/offer-listing/...">`, which the extractor still picks up as a separate anchor candidate. Its title ("From: $489.95") doesn't look like a product, and its assigned price (via the Amazon helper) is the SAME $649.95 as the title anchor — so the LLM either drops it or merges it with the title candidate. Not worth a separate filter today; revisit if the LLM ever outputs the From-link as a real listing.
- Amazon's DOM is the most likely vendor-specific spec to drift on us. Pinning the fixture against the actual class names (`a-price`, `a-text-price`, `a-offscreen`, `s-result-item`, `data-component-type="s-search-result"`) means a future Amazon redesign will fail the test loudly rather than silently degrading prices.

**Out of scope**: extending the same per-vendor structural-selector pattern to other big-box sites (Target, Walmart, Best Buy). Each would need its own helper, and Phase 19's vendor-reach work (tasks 2-4) needs to land first to know which ones are even worth the effort.

**Refines**: ADR-037 (which introduced the wide ancestor walk that caused this regression). ADR-037's broader heuristic is unchanged — the Amazon selector is an override, not a replacement.

---

## ADR-038 — Save-time probe gate is hard-failure-only; refines ADR-037

**Status**: ACCEPTED

**Date**: 2026-05-04

**Context**: ADR-037 introduced a save-time probe at `/api/onboard/save` that demoted any `universal_ai_search` URL with 0 JSON-LD listings AND <3 product-URL anchors to `sources_pending`. The first live save through the new gate (Bose profile, 2026-05-04 03:33 UTC) demoted backmarket — the one universal_ai vendor we knew worked in production — because the TS-side raw `fetch` got the same Cloudflare challenge that Python `httpx` hits, then concluded "no JSON-LD, no anchors." But the production worker uses AlterLab, which renders backmarket fine. Same fate for walmart, crutchfield, reebelo. Net effect: a working profile lost every universal_ai source that the conservative gate didn't recognise, leaving only ebay_search.

The conservative gate was correct in intent (don't ship dead URLs to production) but wrong in practice (it can't see the production fetch tier).

**Decision**: The probe is now **hard-failure-only**. It demotes a URL only when:

1. **Network error** — DNS failure, connection refused, abort/timeout (8s).
2. **HTTP ≥ 400** — explicit 4xx/5xx from origin (the URL doesn't resolve to a real page anywhere).
3. **Body < 500 bytes** — there's no real content to extract from regardless of fetch tier.

What we no longer demote on:
- 200 status with empty / Cloudflare-challenge / React-shell body. The production AlterLab + anchor + LLM tier may extract listings the TS probe can't see.
- 0 JSON-LD listings AND 0 product-URL anchors. Same reason — JSON-LD coverage is sparse on collection pages and the production anchor + LLM tier handles the rest.

The probe still records `jsonldCount` and `anchorCount` on the result object; they're now diagnostics surfaced in the `probeReports` response, not gate inputs.

**Consequence**:
- Backmarket-class URLs (Cloudflare-challenged but production-renderable) stay in `sources` instead of getting demoted.
- The gate becomes a sanity check (404 catcher / typo catcher) rather than a correctness gate.
- The user is trusted to not add dead URLs; if a URL in `sources` truly can't extract in production, the worker run will record `fetched: 0` for that source and the user can demote manually. We've decided that's a better outcome than auto-demoting URLs the user already approved.
- The ADR-037 description of the gate is now accurate ONLY for the decision history — the actual code behaviour is what this ADR describes.

**Migration note** (Bose profile, 2026-05-04): the existing bose-nc-700-headphones profile has 6 universal_ai_search URLs in `sources_pending` that the old gate demoted. Those won't auto-promote — the user has to either re-save through the onboarder (which re-runs the now-relaxed gate) or hand-edit profile.yaml to move them back. There's no auto-migration step; the design is "gate runs at save-time only".

**Refines**: ADR-037 (the JSON-LD tier and anchor heuristic v2 from that ADR are unchanged; only the gate policy is revised).

---

## ADR-037 — Universal adapter quality pass: JSON-LD tier, anchor heuristic fixes, save-time probe gate

**Status**: ACCEPTED

**Date**: 2026-05-03

**Context**: Going into Phase 15, the universal adapter only worked on backmarket. Every other live URL the onboarder added to a Bose profile (bhphotovideo, bestbuy, gazelle, walmart) returned 0/0 fetched/passed. Three failure modes, three different fixes — captured here as one ADR because they were planned and shipped together.

**Decision**:

1. **JSON-LD tier added to `_extract_html` pipeline** in [worker/src/product_search/adapters/universal_ai.py](../worker/src/product_search/adapters/universal_ai.py). Walks every `<script type="application/ld+json">` block, recurses through `@graph` / `itemListElement`, extracts `name` + `offers.price` + `url` from any `Product` (single, list-of-Offers, or AggregateOffer with `lowPrice`). Runs BEFORE the anchor + LLM tier; when it yields ≥1 listing, the adapter returns immediately and the LLM is **not called**. Listings carry `attrs.extractor = "jsonld"` for observability. Handles malformed JSON-LD blocks (skipped), `@type` as string OR list, European decimal commas, condition URLs (`schema.org/UsedCondition` → `"used"`). Already shipped earlier in Phase 15 (commit `46e42eb`).

2. **Anchor heuristic redesigned around per-canonical-URL merging** in the same file. Pre-Phase-15 the extractor walked anchors top-to-bottom and dropped any whose canonical URL had already been seen — which broke the dominant Shopify card pattern where the title-anchor and price-anchor are siblings pointing at the same product URL but in DIFFERENT subtrees. Concrete fixes:
   - **Two-pass design**: collect raw anchors first into `Map<canonical, [raw, ...]>`, then merge each group — best title (longest non-empty), union of price hints, longest context. The dropped anchor's price hints survive into the kept candidate.
   - **Image-alt fallback** in `_anchor_title`: when an anchor's text is empty (Target's `<a><img alt="..."></a>` shape), check descendant `<img>` alt, then aria-label, then title. Recovers ~90% of Target's product titles.
   - **Wider ancestor walk**: `_ancestor_card_text` bumped from 4 hops / 600 chars to 6 hops / 1500 chars. Lets the headphones.com Shopify card find the price in a sibling `card__content` 5 hops up that the old walk couldn't reach.
   - **Path-prefix nav filter** in new `_looks_like_nav_path`: Shopify's `/pages/contact-us`, `/blogs/buying-guides`, big-box `/store-locator`, `/account`, etc. all get disqualified by URL — the bare `_looks_like_product_url` heuristic was passing them because their last segment happens to be hyphenated and ≥6 chars.
   - **`_UI_CHROME_TEXTS` expanded** with the high-frequency nav strings observed in fixtures: "contact us", "about us", "buying guides", "ranking lists", "weekly ad", "registry & wish list", "track order", "find stores".

3. **TypeScript probe + save-time gate**: [web/lib/onboard/probe-url.ts](../web/lib/onboard/probe-url.ts) ports just the JSON-LD extractor and a coarse product-URL anchor count from the Python adapter — no LLM call, no `selectolax` (regex-based block extraction is enough for JSON-LD; URL-pattern matching is enough for the anchor count). [web/lib/onboard/gate-universal-ai.ts](../web/lib/onboard/gate-universal-ai.ts) wraps the probe and is called from [web/app/api/onboard/save/route.ts](../web/app/api/onboard/save/route.ts) on the structured-`draft` save path. For each `universal_ai_search` source on the draft, we probe in parallel (8s per-URL timeout). 0-candidate URLs are MOVED to `sources_pending` with the original source body intact plus a `note` containing the failure reason. Probe reports flow back to the client in the response so `OnboardChat.tsx` can surface them on the error path; the success path logs them to console and proceeds (the user will see them in the committed YAML's `sources_pending` block).

**Consequence**: Phase 15 done-when checklist:
- JSON-LD tier ✅ (5 tests in `test_universal_ai.py` pin Shopify ItemList, AggregateOffer, malformed-block tolerance).
- `cli probe-url` ✅ (4 tests in `test_cli.py`; supports `--render` for AlterLab-required pages).
- Anchor heuristic ✅: against the new headphones.com fixture the extractor now yields **25 candidates with 24 carrying price hints and 25 carrying titles** (was 35 candidates, 0 prices, mostly nav). Against target.com it yields **47 candidates with 46 prices and 47 titles** (was 50 candidates, 0 prices, ~95% empty titles).
- Real-vendor fixtures ✅: 3 new committed (headphones-com-shopify-collection, target-search-bose, bhphotovideo-search-bose) plus 3 prior (amazon, backmarket, gazelle). 4-of-6 yield ≥3 listings via the offline test.
- Onboarder gate ✅: `gateUniversalAiUrls` → `/api/onboard/save` → demoted URLs land in `sources_pending` with the failure note.
- 159/159 worker tests pass; web `tsc` + `next build` green.

**Trade-offs noted**:

- The TS probe is a STRICT SUBSET of the Python adapter — JSON-LD detection works correctly, but the production anchor + LLM tier can extract listings the TS probe will miss (because the TS port doesn't run an LLM). Net effect: some URLs that would work fine in production get demoted to `sources_pending` at save time. The user can manually promote them later if they know the URL works. This is conservative-fail rather than permissive-fail, which is the right default — onboarder shouldn't ship dead URLs.
- The wider ancestor walk (1500 chars) does pull noisy prices into candidate context on small pages where the entire content fits in 1500 chars. The synthetic test fixture demonstrates this (every candidate gets all four section-level prices). Real fixtures (headphones.com, target.com) don't suffer because their per-card text exceeds 1500 chars before the walk crosses card boundaries. The downstream LLM is the backstop that picks the right price for each title — this hasn't regressed in any fixture-pinned test.
- B&H Photo Video, Best Buy, Crutchfield, Walmart, Newegg, Adorama, Sweetwater all fail rendered AlterLab probes (geofence / fully client-rendered React shell / Cloudflare turnstile / 504 from AlterLab itself). These are out of reach for both the Python adapter and the TS probe today. Documented as known-blocked in the test fixture set (`bhphotovideo-search-bose.html` is the regression fixture).

**Out of scope**: Per-vendor adapters (would be Tier-A native work, separate phase). Onboarder tool-call pattern where the assistant invokes `probe_url` mid-conversation (the save-time gate satisfies the brief's "URLs scoring 0 land in `sources_pending`" requirement with materially less complexity than tool-call plumbing in the chat route).

---

## ADR-035 — Run-now UX wipe + drop `?status=completed` from Actions API lookup; refines ADR-032

**Status**: ACCEPTED

**Date**: 2026-05-03

**Context**: ADR-032 stacked four cache defenses (Next fetch `no-store`, raw.githubusercontent.com `?_cb=` query buster, `force-dynamic` on the product page, `window.location.reload()` after run completion) so a Run-now click reliably surfaces the just-pushed report. Despite all four, the user kept reporting "stale screen after Run-now." PROGRESS.md flagged this as a "fifth cache layer" investigation queued for Phase 15.

Investigation in this session found two distinct issues wearing the same costume:

1. **The previous report sits on screen for several minutes during the run.** Even when the post-reload page renders fresh content, the user spent the entire workflow-running window staring at numbers from the prior run. That itself is what drives the "is it stale?" anxiety the four cache layers were meant to fix.

2. **GitHub Actions `?status=completed` view is eventually consistent.** `getLastCompletedRun` queried `/runs?event=workflow_dispatch&status=completed&per_page=20`. After a fresh completion, the just-finished run is missing from that filtered index for tens of seconds. The lookup returned the *previous* completed run, and the "Last run completed …" footer showed a stale timestamp — even when the report content above it was the fresh one. From the user's perspective on 2026-05-03: footer showed an 8:43 AM ET morning run right after a 1:41 PM ET dispatch had completed and the report had updated.

**Decision**:

1. **UI wipe while a run is in flight.** New client-only pub/sub store at [web/app/[product]/runState.ts](../web/app/[product]/runState.ts) (backed by `useSyncExternalStore`) and a `<ReportSection>` wrapper at [web/app/[product]/ReportSection.tsx](../web/app/[product]/ReportSection.tsx) around the report markdown + `RunInfoFooter`. `RunNowButton` mirrors its state into the store at every transition; the wrapper swaps the rendered article for a spinner card when running. The wipe stays through the brief `done` state until `window.location.reload()` swaps the whole page. Unmount cleanup clears the flag so navigating away mid-run doesn't leak hidden state to a later visit. Rationale: the user can't act on numbers that aren't on screen — the wipe sidesteps any cache layer the four-layer stack hasn't accounted for, by removing the surface where staleness can be observed at all.

2. **Drop `?status=completed` from `getLastCompletedRun`** in [web/lib/dispatch.ts](../web/lib/dispatch.ts). Query `?event=workflow_dispatch&per_page=20` (no status filter) and check `match.status === 'completed'` in code, with a fallback to the most-recent completed run if the slug-match find misses. The unfiltered listing is updated eagerly; the filtered index is not. Same pattern that `getLatestOnDemandRun` (used by `/api/run-status` polling) already follows — which is why polling never had the lag.

**Consequence**: User verified live on 2026-05-03 PM that Run-now click → report area wipes → workflow runs → page reloads with fresh content + correct footer timestamp. The "stale display after Run-now" class of complaints is closed because there is no longer a window where the user observes previous-run data. The four-cache stack from ADR-032 still does its job; ADR-035 adds belt-and-suspenders against any layer not yet accounted for.

**Lesson**: When GitHub returns a query parameter that "narrows" results, double-check whether it goes through a separately-indexed view. `?status=completed` does. So do many `?state=…`, `?type=…`, and similar filters across GitHub's REST API. Where freshness matters, query unfiltered and narrow in code — the bandwidth savings of a server-side filter are not worth the consistency lag.

**Refines**: ADR-032 (adds a UI-side defense, fixes an orthogonal Actions API consistency bug). Doesn't supersede — the four cache layers in ADR-032 still do load-bearing work.

---

## ADR-034 — Onboarder swap to Claude Haiku 4.5 + structured-intent JSON; supersedes ADR-015

**Status**: ACCEPTED

**Date**: 2026-05-03

**Context**: After ADR-015 picked Claude Sonnet 4.6 (Phase 10) and the Phase 11 reset moved to Zhipu GLM-5.1 ($2/$8 per M tokens) for cheaper interview cost, two structural problems remained:

1. The model emitted full YAML in every turn. A single dropped brace or stray comment broke the right-pane preview and the save flow. Sliding-window compression made this worse — older turns whose YAML was authoritative could be evicted.
2. Mid-conversation the model would forget the slug or display_name and either ask again or invent a new one. The previous mitigation ("preserve messages[0]") covered edit-mode but not new-profile sessions where slug was decided in turn 3.

**Decision**: Onboarder is `anthropic/claude-haiku-4-5` ($1/$5 per M tokens) with three structural changes:

1. **Native `web_search_20250305` server-tool.** Anthropic runs the search server-side and feeds results back into the same streaming response — no multi-turn tool round-tripping in our route.
2. **Ephemeral prompt caching** on the system prompt (~4500 tokens). Cache reads cost 0.1× input rate. After turn 1 the system prompt is essentially free.
3. **`<state>` ledger + `<draft>` intent JSON per turn.** Every assistant message ends with two single-line JSON blocks: a running decisions ledger and a structured intent that mirrors the YAML schema 1:1. Server-side `web/lib/onboard/render-yaml.ts` deterministically renders YAML at save time. The sliding window now compresses dropped middle turns into one synthetic assistant turn containing the latest `<state>` block, so the model always sees a complete decision history regardless of how long the conversation gets.

**Consequence**: Phase 14 bench (15-turn session about NC headphones <$300, 7 web searches, slug confirmed at turn 3 and re-confirmed at turn 13):
- Total cost: $0.1779 (dominated by 2 vendor-discovery turns at $0.04–$0.06 each from search-result tokens being cached at creation rate). Non-search turns averaged $0.007 each.
- Slug `nc-headphones-under-300` and display_name persisted across all 15 turns including an explicit memory probe at turn 13.
- Web search worked end-to-end via Anthropic's server-tool path.
- Cost target (≤30% of GLM-5.1 baseline) is **met on non-search turns only**. On search-heavy turns, cost is dominated by web-search-result token volume which is largely vendor-architecture-independent. Target should be re-stated as "≤30% per non-search turn" in Phase 15+ planning.

The "model dropped a closing brace in YAML" failure class is gone — the only YAML the user sees is what `js-yaml.dump()` produced from a validated JSON object.

**Supersedes**: ADR-015 (Sonnet 4.6 Phase 10 onboarding model). ADR-013's "LLMs only suggest sources, never extract listings" architectural commitment still holds — Haiku's web_search is suggestion-only at onboarding time. ADR-014 is unrelated (it's about `/api/dispatch` auth).

**Open follow-ups for Phase 15+**:
- Tighten `web_search.max_uses` from 5 → 2 per turn (the bench saw 3+4 searches in two consecutive turns, which is wasteful).
- Consider running a head-to-head GLM-5.1 bench with the new prompt format if the absolute cost target needs revisiting.

---

## ADR-033 — Tier-3 vendor fetcher swaps from ScrapFly to AlterLab; supersedes ADR-030

**Status**: ACCEPTED

**Context**: ADR-030 picked ScrapFly as the Tier-3 vendor fetcher (`render_js + asp` for the Cloudflare/Datadome class of sites). Operationally, ScrapFly's free credit budget burned out within a few days of normal Bose-700 runs and the universal_ai path silently regressed to curl_cffi (which can't get past Cloudflare on SPAs like backmarket). User decided to swap providers to AlterLab, whose free tier is more generous for the same render_js workload.

A continuation 12 commit (33553f8) renamed the integration from ScrapFly to AlterLab, but **the wire format was inferred from ScrapFly's shape and never exercised against the real AlterLab API**. The accompanying unit test mocked the same fictional shape, so the green test was misleading. Phase 13 verification caught this: every `_fetch_via_alterlab` call returned 404, the silent fallback in `_fetch_html` routed every run through curl_cffi, and the on-demand reports for `bose-nc-700-headphones` quietly went 3→0 listings on backmarket between continuations 10 and 12.

**Decision**:
1. Tier-3 fetcher is AlterLab. Env gate is `ALTERLAB_API_KEY` (configured in GH Actions secrets and `.env.example`). ScrapFly's `SCRAPFLY_API_KEY` is removed from the adapter.
2. Wire format (per https://alterlab.io/docs/api/rest):
   - `POST https://api.alterlab.io/api/v1/scrape`
   - Header `X-API-Key: <key>`
   - Body `{"url": <target>, "sync": true, "formats": ["html"], "advanced": {"render_js": true}}`
   - Response `{"status_code": <origin>, "content": {"html": "..."}, ...}`
   - `formats: ["html"]` makes `content` deterministically an object (vs a bare string in some sync responses).
3. Fallback semantics are unchanged from ADR-030: AlterLab failure → log + fall through to curl_cffi → httpx, EXCEPT when AlterLab returns HTTP 401/403/429 (auth/quota), which bubbles up as `RuntimeError("AlterLab API issue: ...")` so cli.py's existing surface displays a "Scraping API Issue" banner in the report.
4. Per-vendor 0/0 cases are NOT AlterLab failures and must not be misattributed as such — the worker logs the alterlab status_code and body length so extraction-vs-fetch failures are distinguishable from the GHA log alone.

**Consequence**:
- Fix landed in [worker/src/product_search/adapters/universal_ai.py](worker/src/product_search/adapters/universal_ai.py) (`_fetch_via_alterlab`) and [worker/tests/test_universal_ai.py](worker/tests/test_universal_ai.py) (`test_alterlab_fetch_path_used_when_key_set`). Mock now matches the real wire format; if AlterLab changes their REST shape this test will fail loudly.
- Live verification (Phase 13): both Bose universal_ai vendors return origin status 200 with non-empty rendered HTML via AlterLab. backmarket returns a Cloudflare "Just a moment…" challenge body (~32KB) — `render_js: true` alone isn't enough for backmarket's anti-bot tier; pursue stronger AlterLab options (proxy mode / tier escalation) in Phase 15. gazelle returns a soft-404 body (~305KB with `<link rel="canonical" href="/404">`); the URL `/collections/headphones` in the profile is wrong — flag for the user to re-onboard.
- Lesson: a unit test whose mock matches the implementation rather than the upstream API contract is worse than no test, because it lends false confidence. For each future SaaS integration, the integration's first test must be a captured fixture from a real call, not a hand-stubbed envelope.

ADR-030 is **SUPERSEDED** by this ADR.

---

## ADR-032 — Run-now freshness: `force-dynamic` on the product page + `window.location.reload()` after run completion

**Status**: ACCEPTED

**Context**: The Run-now flow was supposed to land a freshly-pushed
report on the user's screen as soon as the GH Actions workflow
completed. Three previous waves had stacked defenses:

- Wave 5 (`b2b23d3`): switch report-fetches to `cache: 'no-store'` so
  the Next.js fetch cache doesn't serve stale data.
- Early 2026-04-30 session: append `?_cb=${Date.now()}` to the
  `raw.githubusercontent.com` URL because that CDN ignores
  `cache: 'no-store'` headers.
- Wave 12d ScrapFly commit (`d8edcc2`): rewrote the PWA service
  worker (`v1` → `v2`) to stop stale-while-revalidate'ing the RSC
  payload after `router.refresh()`.

After all three, this session's user — on a desktop browser, not the
PWA — still saw the previous run's report (an exact match for commit
`18b750a`'s numbers, two report-commits behind origin's HEAD). Even a
hard-refresh before clicking Run-now didn't help. Two layers remained
unaddressed:

1. **Vercel edge HTML/RSC cache.** Despite `await searchParams` and
   `cache: 'no-store'` fetches inside the page, nothing in the route
   was an explicit "this is dynamic" signal that the platform's edge
   layer was guaranteed to honour. With the right combination of CDN
   config and route heuristics, Vercel can serve an HTML/RSC payload
   from a previous render even on a hard refresh.

2. **`router.refresh()` doesn't invalidate the server-side cache.**
   Per the explicit warning in
   `node_modules/next/dist/docs/01-app/03-api-reference/04-functions/use-router.md`
   line 55: *"`refresh()` could re-produce the same result if fetch
   requests are cached."* It only re-fetches the RSC payload; it
   does not bypass any underlying cache layers. So even if every
   data layer was wired correctly, the client-side trigger after
   run completion was the wrong tool for the job.

**Decision**:

1. **Add `export const dynamic = 'force-dynamic'`** to
   `web/app/[product]/page.tsx`. Defensive: makes the route
   un-prerenderable and un-cacheable at the Vercel edge regardless
   of how the underlying fetch heuristics evolve.

2. **Replace `router.refresh()` with `window.location.reload()`** in
   `web/app/[product]/RunNowButton.tsx` after the run completes
   successfully. A full page reload bypasses Vercel's edge cache,
   the browser's HTTP cache, and Next's RSC client cache — and
   re-runs SSR with a fresh `?_cb=` cache-buster. We accept the
   visual flash because the freshness guarantee is the user's
   explicit ask.

The full defensive stack on a Run-now click is now four layers:

| Layer | Defense |
|---|---|
| Next.js fetch cache | `cache: 'no-store'` on `getReportContent`, `getProductReports` |
| GitHub raw CDN (Fastly) | `?_cb=${Date.now()}` query-string buster |
| Vercel edge HTML/RSC cache | `export const dynamic = 'force-dynamic'` (this ADR) |
| Browser HTTP / Next router cache | `window.location.reload()` (this ADR) |

**Consequence**: Run-now reliability becomes a hard guarantee, not a
heuristic. Trade-off: a brief flash on the reload (vs. the smooth
RSC swap that `router.refresh()` provided). For a one-user app where
freshness > smoothness, acceptable. The `useTransition` /
`useRouter` scaffolding in `RunNowButton.tsx` is no longer needed
and was removed.

---

## ADR-031 — Per-run CSV under `reports/<slug>/data/`, replacing per-day `worker/data/<slug>/<date>.csv`

**Status**: ACCEPTED

**Context**: The previous CSV layout had two distinct problems:

1. **Wrong location for prod persistence.** `worker/data/` is in
   `.gitignore` and is not uploaded as a GH Actions artifact (only
   `filter_logs/` and `llm_traces/` are). On the prod runner, both
   the SQLite DB and the CSV were ephemeral — when the job ended,
   they vanished. But the synth report's standard footer said
   *"the full set is persisted to SQLite and the daily CSV"* — true
   locally, false in prod. The user noticed: report claimed CSVs
   existed, but they didn't.

2. **Per-day, not per-run.** `default_csv_path` returned
   `worker/data/<slug>/<YYYY-MM-DD>.csv` and `write_snapshot_csv`
   opened with mode `"w"`. Re-running on the same day overwrote the
   previous run's CSV. The SQLite layer correctly preserved every
   run (composite PK `(url, fetched_at)`) but the CSV did not.
   User explicitly asked for "each run's full list, not just
   overwrite daily."

The repo-as-database architecture (ADR-002, ADR-004) already
established that anything we want to outlive a workflow run has to
be committed back. The synth `.md` report does this; the
`.filter.jsonl` does this. The CSV should too.

**Decision**:

1. **Move CSVs from `worker/data/<slug>/<date>.csv` to
   `reports/<slug>/data/<YYYY-MM-DDTHH-MM-SSZ>.csv`.** Inside the
   committable `reports/` tree so the existing `git add -A` step in
   both workflows picks them up automatically alongside the .md
   report. One CSV per run (timestamp-named) so same-day reruns
   accumulate rather than overwrite.

2. **`default_csv_path` signature changes** from
   `(slug, snapshot_date: date)` to `(slug, fetched_at: datetime)`.
   The cli.py call site captures `run_started_at = datetime.now(tz=UTC)`
   once at the top of the persist block and passes it through.
   `snapshot_date` (used for the report `.md` filename and diff
   queries) is unchanged — that stays per-day so `2026-05-01.md`
   cleanly reflects "today's report" regardless of how many times
   the user clicked Run-now.

3. **UTC-normalise the filename timestamp** regardless of the
   caller's tz, so dev (local TZ) and prod (UTC GHA runner) produce
   identical paths for the same instant. Filename uses `-` instead
   of `:` for Windows compat (NTFS reserves `:` for ADS).

4. **SQLite stays at `worker/data/<slug>/listings.sqlite`** —
   gitignored, ephemeral on GHA, useful locally for the `diff`
   command. Not changed; CSVs are the prod-persisted artifact.

5. **Report wording updated** to drop the misleading "SQLite and the
   daily CSV" claim. Reports now say *"persisted to a per-run CSV
   under `reports/<slug>/data/`"* which is true in both prod and dev.

**Consequence**: Every Run-now click — and every scheduled run —
now produces a permanent, timestamped, browseable record of every
listing the worker considered. Repo size grows by ~20-30 KB per
run; at 1 product × 1 daily run + occasional ad-hoc, that's ~10 MB
per year, manageable. If repo size becomes an issue later, archival
of older CSVs to a release artifact or external store is a
straightforward future ADR.

---

## ADR-030 — ScrapFly as the Tier-3 vendor fetcher (env-gated render_js + asp), curl_cffi/httpx remain the cheap tiers

**Status**: SUPERSEDED by ADR-033 (provider switched to AlterLab; same fallback semantics). (extends ADR-029's fetch-priority chain)

**Context**: ADR-029's `_fetch_html` had two tiers — `curl_cffi`
(Chrome TLS-fingerprint impersonation, free) and `httpx` (plain
fallback). Real-vendor runs on 2026-04-30 (commits `1237737` and
`18b750a`) confirmed that those tiers cover server-rendered
storefronts but not the bot-protection landscape modern e-commerce
actually uses. Five vendors tested across two runs:

| Vendor | Result | Why |
|---|---|---|
| crutchfield.com | 0/0 | Heavy client-side React, products loaded post-render |
| adorama.com | 0/0 | Akamai-fronted, full bot challenge |
| headphones.com | 0/0 | Modern Shopify theme, JS-heavy product cards |
| audio46.com | 0/0 | Same — modern Shopify theme |
| bhphotovideo.com | 0/0 | Search results page is partially client-rendered |

The curl_cffi-only path got past TLS fingerprint blocks (no 403s)
but the post-fetch HTML simply didn't contain product anchors with
`$X.XX` price tokens because those elements are written into the
DOM by JavaScript at runtime. `_extract_candidates` therefore
returned [] every time.

The user explicitly chose ScrapFly when offered the JS-render
options (vs. self-hosted Playwright). ScrapFly bills credits, runs
real headless Chrome, rotates residential proxies, and solves
common JS challenges. Free tier is ~1k credits/month; expected
usage at this project's scale (3 vendors × daily run × 5-10 credits
each = ~270/month) sits comfortably inside free.

**Decision**:

1. **New `_fetch_via_scrapfly(url, key)` helper** in
   `worker/src/product_search/adapters/universal_ai.py` that calls
   `https://api.scrapfly.io/scrape` with:
   - `key=<SCRAPFLY_API_KEY>`
   - `url=<vendor URL>`
   - `render_js=true` (full headless-Chrome render)
   - `asp=true` (anti-scraping protection: residential proxies +
     challenge solving)
   - `country=us`
   It parses ScrapFly's JSON envelope and returns
   `(html, origin_status_code, "scrapfly")` so the rest of the
   adapter doesn't care which tier produced the HTML.

2. **`_fetch_html` priority is `scrapfly → curl_cffi → httpx`**.
   ScrapFly is tried first ONLY when `SCRAPFLY_API_KEY` is set; the
   key check is a single env-var read so no-key environments don't
   pay any startup cost. If the ScrapFly call raises (network error,
   API outage, 5xx), the helper logs a warning and falls through to
   curl_cffi — so a ScrapFly outage cannot zero a run for vendors
   that don't actually need rendering.

3. **Both workflows propagate the secret**.
   `.github/workflows/search-on-demand.yml` and `search-scheduled.yml`
   now declare `SCRAPFLY_API_KEY: ${{ secrets.SCRAPFLY_API_KEY }}`
   in the `env:` block of the search step. `.env.example` documents
   the variable so local-dev runs can use it too.

4. **No retry, no per-vendor opt-out**. The first iteration treats
   "ScrapFly is on for all universal_ai_search sources or off for
   all" as the only knob. If a specific vendor proves expensive or
   reliably succeeds at the curl_cffi tier, a future ADR can add a
   per-source `render: false` flag in the profile YAML to skip
   ScrapFly for that one.

**Consequence**: Adding a vendor URL via onboarding now genuinely
covers JS-rendered + Cloudflare-fronted sites for the user, not
just server-rendered Shopify-style stores. The architecture from
ADR-029 — anchor-first candidate extraction with no LLM URL
invention — is unchanged; ScrapFly only changes how the HTML
arrives. A vendor that uses heavier-than-expected anti-bot still
returns 0/0 (e.g. some Datadome-protected sites can defeat
ScrapFly's default ASP profile), but those should now be the
exception rather than the rule.

**Trade-offs**:
- **Cost**: 5-10 credits per JS-rendered page. The user's free
  tier is 1k credits/month. At 3 vendors per profile × daily
  scheduled run = ~270/month for one product, ~540 for two — well
  inside free. If usage grows past free, ScrapFly's paid tier
  starts at $30/month (50k credits).
- **Latency**: JS render adds ~5-15s per vendor on top of the cheap
  fetch baseline. A run with 3 universal_ai sources jumps from
  ~10s to ~45s. Acceptable for both scheduled (no UI wait) and
  Run-now (the user is already polling for ~3-4 minutes for the
  full pipeline).
- **Run-cost panel doesn't show ScrapFly cost**. The panel sums
  LLM token spend from `LAST_RUN_USAGE` rows; ScrapFly is an HTTP
  fetch, not an LLM call, so it's invisible there. The user
  monitors ScrapFly spend in their ScrapFly dashboard. A future
  improvement could parse `result.cost` from ScrapFly's JSON
  envelope and add it as a synthetic cost row.
- **Single-vendor outage failover is binary**. If ScrapFly is down
  AND the vendor needs JS render, the run produces 0/0 for that
  vendor (and falls back to curl_cffi which also produces 0/0).
  Acceptable — alternative would be queue + retry, which is more
  complexity than the issue warrants.
- **Privacy**: vendor page HTML transits ScrapFly's servers. For
  shopping-search use this is fine; would not be appropriate for
  fetching authenticated or private content.

**Reversibility**: Trivial. Unset `SCRAPFLY_API_KEY` and the
adapter reverts to curl_cffi/httpx with no other change. The
ScrapFly call site is one helper function gated by a single env
check.

**Open follow-ups**:
- Surface ScrapFly per-call cost in the Run-cost panel (pull
  `result.cost` from the ScrapFly response).
- Per-source `render: false` profile flag if a specific vendor is
  observed to succeed at curl_cffi (saves credits on that vendor).
- Consider adding `wait_for_selector` for vendors whose product
  cards lazy-load after the initial render fires.

---

## ADR-029 — Universal vendor scraping: anchor-first extraction + Chrome TLS impersonation, no LLM URL invention

**Status**: ACCEPTED (refines ADR-021's "Universal AI Adapter")

**Context**: ADR-021 introduced `universal_ai_search` so the onboarding
flow could add arbitrary vendor URLs without a developer writing a
custom adapter. The first implementation:

1. Used `httpx` with a Chrome User-Agent string but Python's TLS
   fingerprint, which trips most modern bot-detection (Cloudflare's
   default rules, Akamai, PerimeterX) on contact even though the
   header looks Chromy.
2. Used `glm-5.1` for extraction. The 2026-04-30 ai_filter debug arc
   already established that GLM-5.1 is a reasoning model that ignores
   `response_format=json_object` and dumps chain-of-thought into
   `content` (see ADR-023 + memory entry `reference_zai_openai_shim`).
   Same model was still wired into universal_ai.
3. Stripped `<nav>`, `<footer>`, `<svg>` from the DOM, then fed the
   resulting body text to the LLM and asked it to invent
   `{title, price, url, condition}` objects from prose. URL
   hallucination was guarded only by a verbatim-substring check
   against the original raw HTML — which the LLM frequently failed
   when the URL ran across newline-introducing whitespace in the
   cleaned text.
4. Had no unit tests, no fixture, no `LAST_RUN_USAGE` capture for the
   cost panel, and no anti-bot story beyond the User-Agent header.

The user wants universal vendor support to actually work, including
overcoming bot blocking. The 2026-04-30 cost-panel work (ADR-028
context) made it cheap to surface per-source LLM cost, but
`universal_ai_search` was invisible.

**Decision**:

1. **Anchor-first candidate extraction**. `_extract_candidates(html,
   base_url)` walks every `<a href>` in the DOM with `selectolax`,
   resolves each href to absolute via `urljoin`, and keeps only
   anchors that are either (a) hosted on a path that looks
   product-like (`/product/`, `/products/`, `/p/`, `/dp/`, `/itm/`,
   etc., or a slug-shaped last segment) OR (b) have a `$X.XX`-style
   price token in their nearest "card-like" ancestor (climbing up to
   4 parents and stopping when the ancestor's text exceeds 600
   chars). Search-results URLs (`/search`, `?q=`, `/collections/`,
   `/categories/`) are filtered out so we never emit a category page
   as a "listing." Dedupe by canonical scheme+host+path.

2. **LLM picks by index, never by URL**. The candidate list is sent
   to Claude Haiku 4.5 with each entry's `idx`, anchor text, price
   hints, and ≤400-char card context. The LLM returns
   `{idx, title, price_usd, condition}` objects. URLs are looked up
   from `candidates[idx].href` server-side. The LLM therefore cannot
   invent URLs — they are sourced verbatim from the page's DOM, by
   construction. This eliminates a whole class of post-check failure
   without needing a verbatim-substring guard.

3. **LLM swap glm-5.1 → claude-haiku-4-5**. Same model already used
   by `ai_filter` (ADR-023) and as a synth fallback. Reliable JSON
   mode, tolerates short structured payloads. Same prose-tolerant
   `_extract_json` helper as `ai_filter` is mirrored locally in the
   adapter so it stays standalone.

4. **TLS-fingerprint impersonation via `curl_cffi`**. New optional
   import: when the `curl_cffi` package is installed, the adapter
   issues the GET via `cc_requests.get(..., impersonate="chrome")`,
   which negotiates the TLS handshake and HTTP/2 settings frames
   using a real pinned Chrome profile. Server-rendered storefronts
   that gate on JA3/HTTP2-fingerprint accept this. When `curl_cffi`
   is absent, the adapter falls back to plain `httpx` with the same
   header set so existing environments keep working.

5. **Cost-panel integration**. `LAST_RUN_USAGE` is populated after
   each successful call. `cli.py`'s source loop accumulates one
   usage row per `universal_ai_search` source (tagged with the
   source URL so a multi-vendor profile's cost panel disambiguates
   them) and threads them into all three Run-cost build sites
   (success report, post-check stub, zero-pass diagnostic).

6. **Onboarder prompt update**. The `web_search` section and
   interview step 5 now actively direct the AI to use `web_search`
   for vendor discovery and to convert each confirmed vendor URL
   into a `universal_ai_search` source (multiple entries are fine).
   The "Allowed source IDs" section explicitly notes that
   `universal_ai_search` URLs must point at category / search /
   collection pages, not single product detail pages, and that
   sites requiring JS rendering or solving a Cloudflare challenge
   will silently return zero listings (so the onboarder should
   suggest alternatives in that case).

7. **Test fixture + unit tests**. A synthetic vendor HTML
   (`worker/tests/fixtures/universal_ai/synthetic_vendor.html`) pins
   the candidate extractor's behaviour: nav/cart/footer chrome are
   skipped, relative and absolute hrefs both resolve, duplicates by
   canonical URL collapse, price hints attach to the right card,
   and priceless-but-product-shaped anchors survive into the LLM
   payload (so the LLM can decide). End-to-end `fetch()` tests stub
   both `_fetch_html` and `call_llm`, asserting verbatim URLs in
   the resulting Listings, no crash on out-of-range LLM idx values,
   and tolerant parsing of a prose preamble before the JSON.

**Consequence**: A profile can now declare one or more
`universal_ai_search` sources with arbitrary vendor URLs, and the
adapter will:
- Get past TLS-fingerprint bot blocks on most server-rendered sites.
- Extract real product anchors with prices structurally, not via LLM
  guesswork.
- Never invent a URL (URLs come from the DOM by index lookup).
- Surface per-source LLM cost in the daily report.

**Trade-offs**:
- Sites that require full JS rendering (React/Vue SPAs, Cloudflare
  challenge pages, Datadome) still return zero listings. The
  adapter logs a clear "no anchor candidates extracted" warning so
  the failure mode is debuggable from the worker log. A Tier-3
  Playwright/headless-browser path is intentionally deferred until
  a profile actually needs it — and may never be needed if the
  user's vendor list happens to be all server-rendered.
- The candidate extractor is heuristic: "looks product-like" is a
  set of URL-substring signals plus a price-nearby check. Sites
  with unusual URL schemes (e.g. all-numeric SKU paths, no slug)
  may need a tweak. The synthetic test fixture makes such tweaks
  observable as test diffs rather than silent regressions.
- `curl_cffi` adds a transitive dependency on libcurl-impersonate
  (ships pre-built wheels for Windows/macOS/Linux on Python 3.12).
  CI install time grows by ~5-10 seconds on the first warm. This
  is acceptable for the value of getting past basic bot blocks.
- Cost: each `universal_ai_search` source is one Haiku 4.5 call
  per run. ~1-2k input tokens (anchor candidates) + ≤500 output
  tokens, so ~$0.005 per vendor URL per run. A profile with three
  vendor URLs running daily costs ~$0.45/month on universal_ai
  alone — small but visible in the cost panel.

**Reversibility**: Moderate. The adapter's old call shape (LLM
extracts directly from page text) is gone; reverting would mean
restoring the old `universal_ai.py` from git. The `curl_cffi`
dependency is benign and can be left in place even if reverted.
The onboarder prompt edits are easily reverted by ADR-021's
prior wording.

**Open follow-ups**:
- Tier-3 JS-render path (Playwright or a hosted scraping service
  like ScrapFly / BrowserBase) gated by env var, only when a
  profile needs it. Cost is non-trivial; defer until needed.
- Live smoke test: pick one real vendor URL the user trusts, run
  `cli search` on a profile pointing at it, capture a fixture from
  the actual response if useful. Not a CI test (would couple to a
  third-party site's HTML), but a useful one-off validation step.
- The candidate extractor currently caps at 80 candidates per
  page. Larger result pages would need pagination support — defer
  until a profile actually paginates.

---

## ADR-028 — Numbers belong to Python; words belong to the LLM (Bottom line and Flags become deterministic; LLM only writes Context)

**Status**: ACCEPTED (refines ADR-001's split, partially supersedes
ADR-027 — the retry remains as a courtesy but is no longer the primary
defense)

**Context**: The previous structure left three sections to the LLM —
Bottom line, Flags, Context — guarded only by the post-check rejecting
fabricated numbers in the rendered report. ADR-027 added a single
retry with a stricter prompt naming the rejected numbers. This still
failed in production: a 2026-04-30 Bose run produced
`fabricated numbers: ['7.7']` (a computed percentage in the Bottom
line / Context narrative) on both the first attempt and the retry.

The pattern is structural, not promptable: when the LLM is asked to
write narrative *about* listings whose prices it can see, it
intermittently computes comparisons ("X% cheaper", "saves $Y") because
that's what an analyst-style sentence reaches for. Stricter prompts and
retries chase the symptom.

**Decision**:

1. **Bottom line is built deterministically in Python** —
   `build_bottom_line_md(listings, profile)` picks the cheapest passing
   listing (sorted by `total_for_target_usd`, falling back to
   `unit_price_usd` when target totals are not meaningful for the
   product type) and emits a one-sentence summary using only fields
   verbatim from that listing: price clause, seller, source link,
   title, condition. No LLM involvement, so no fabrication risk.

2. **Flags is built deterministically in Python** —
   `build_flags_md(listings, profile)` enumerates the distinct flags
   that appear in the visible listings and emits one bullet each. The
   description text comes from a new optional field
   `FlagRule.description` on the profile, falling back to a built-in
   `FLAG_FALLBACK_DESCRIPTIONS` dict for stable IDs
   (`unknown_quantity`, `low_seller_feedback`, etc.), and finally to
   the bare flag id. Future profiles SHOULD set `description:` per
   flag; the fallback dict is only a safety net.

3. **The LLM writes ONE qualitative paragraph — the Context section
   only.** `synth_v1.txt` is rewritten to ask for prose only, with a
   non-negotiable "ABSOLUTELY NO DIGITS" rule. The model still
   receives the full payload (listings, diff, synthesis_hints) so its
   commentary is informed; the constraint is only on its output shape.

4. **`synthesize()` post-checks the LLM's paragraph** (not the
   assembled report). The existing `post_check` semantics are
   unchanged — any digit in the LLM output that isn't in the input
   payload is rejected — but the surface area is now ~80 words of
   prose instead of three sections of mixed numbers and prose. The
   single retry from ADR-027 survives, but its scope shrinks
   accordingly: "you wrote digits that aren't in the JSON; remove
   them and rephrase qualitatively."

5. **Final report assembly is purely string concatenation in Python** —
   `build_bottom_line_md` + `build_listings_table_md` + `build_diff_md`
   + `build_flags_md` + `**Context.** {llm_paragraph}`. No regex
   extraction of LLM sections.

**Consequence**: The "fabricated computed comparison" failure mode
disappears structurally — the LLM is no longer asked to write
sentences that contain prices, so it no longer reaches for
"$X represents Y% lower than Z." The Context paragraph is now a
qualitative analyst observation ("most listings are used", "the
cheapest tier comes from sellers with sub-99% ratings"), which is
more useful than a paraphrase of the table the user can already see.

**Trade-offs**:
- Context loses phrasings like "$47.97 to $325.00 spread". The
  ranked-listings table directly above shows this; the qualitative
  narrative is more distinctive in any case.
- Bottom line is now templated rather than free-form. Less stylistic
  variety per day; zero fabrication risk and consistent across
  products.
- Adding a new product no longer requires re-tuning the synth prompt
  for its flag vocabulary. Each profile can declare its own flag
  descriptions; otherwise the synthesizer falls back gracefully.

**Reversibility**: Moderate. The deterministic builders (`build_*_md`)
are self-contained pure functions; reverting would mean restoring the
old `synth_v1.txt` and the old `synthesize()` regex-extraction path.
The `FlagRule.description` field is optional and forward-compatible
with profiles written under the old structure.

**Future direction (deferred)**:
- Re-run the Phase 5 benchmark fixtures against the new
  Context-only contract — most criteria (sort_order, all_rows_present)
  no longer apply because they're enforced deterministically. The
  benchmark may collapse to a single "post-check passes on a 10-fixture
  Context paragraph" check.
- Update onboarding to ask the user for `description:` per flag at
  profile-creation time, so the fallback dict is genuinely a safety
  net rather than the common path.

---

## ADR-027 — Synth retries once on `PostCheckError` with a stricter prompt that cites the rejected numbers

**Status**: ACCEPTED

**Context**: Even after ADR-024's swap to GLM 4.5 Flash and the
ranked-listings table being made deterministic, the synth LLM
intermittently fabricates a single percentage or savings amount in
the Bottom line / Context narrative ("$47.97 represents 7.7% lower
than the average"). The base prompt (`synth_v1.txt`) already has
explicit "Do NOT compute percentages, ratios, savings amounts" rules
and even gives forbidden examples; it's not enough. Both Haiku 4.5
(continuation 3) and GLM 4.5 Flash (continuation 5) hit this class
of failure on prod-scale data. The post-check correctly rejects
these as fabricated numbers and ADR-024's stub-report path surfaces
the failure on the web UI, but a hard-fail on the daily report is a
poor user experience when the issue is one stray clause.

**Decision**:

1. **Single retry inside `synthesize()`** when the first attempt
   raises `PostCheckError`. The retry's system prompt is the base
   prompt + a bolted-on "RETRY — PRIOR ATTEMPT REJECTED" section
   that:
   - Names the specific numbers the post-check rejected.
   - Gives explicit anti-pattern phrases to avoid: "X% cheaper",
     "saves $Y", "represents Z%", "lower than the average".
   - Restricts to qualitative phrasing only ("cheapest",
     "second-cheapest", "highest-priced", "lower-end", "mid-range").
   - Says re-emit from scratch with the same section structure.
2. **`PostCheckError` carries the rejected numbers** as
   `.bad_numbers: list[str]` so the retry can cite them without
   parsing the error message string.
3. **Retry only once.** If the second attempt also fails, the
   `PostCheckError` propagates, `cli.py`'s stub-report path runs,
   and the web UI shows the diagnostic. No infinite retry loops, no
   silent fabrication.

**Consequence**: A failure mode that previously cost the user a full
hard-fail of the daily report now usually self-heals on retry. Cost
overhead per run is one extra LLM call only when the first call
fails (rare on quiet payloads, common on prod-scale headphone
listings). The strictness/correctness boundary is unchanged — the
post-check is still the absolute gate; the retry is a courtesy that
gives the model a chance to correct itself when given specific
feedback.

**Reversibility**: Trivial. Remove the retry block in
`synthesize()`. The existing stub-report path absorbs the failure
exactly as before.

**Future direction (deferred)**: If retries don't catch enough of
the percentage class — (a) split Bottom line into a deterministic
first sentence + LLM-supplied "and here's why" clause; (b) drop
Bottom line entirely and have the LLM contribute only Flags +
Context. Both reduce LLM exposure further.

---

## ADR-026 — `brand_candidates` profile field fills in eBay's missing brand for non-RAM categories

**Status**: ACCEPTED

**Context**: eBay's Browse API summary endpoint reliably returns
`brand` for RAM listings (Samsung, Micron, SK Hynix all populate)
but for headphones and other non-RAM categories the field is
frequently `None`. The synthesizer's Brand column then renders
"unknown" for every listing even when the brand is visually obvious
in the title ("Bose Noise Cancelling Headphones 700"). This made
the Brand column useless for the user's first non-RAM product
(Bose NC700) and pushed them to remove it from `report_columns`
entirely.

**Decision**:

1. **New optional profile field**: `brand_candidates: list[str]`,
   validated as a non-empty list of non-blank strings (or absent
   entirely → no inference).
2. **Pipeline integration** as step 2 (after ai_filter, before QVL
   and flags): `infer_brand_from_title(listing, candidates)`
   matches each candidate against the title using a
   case-insensitive word-boundary regex (`\b<token>\b`). First
   match wins; the canonical-cased candidate (the profile's
   spelling) is assigned. Non-None brands are never overwritten —
   adapter-supplied brand is authoritative when present.
3. **Onboarding prompt update**: a new "Brand candidates" section
   tells the AI that for non-RAM categories it should ask the user
   for canonical brand names and put them in `brand_candidates:`.
   For RAM, it can be omitted because eBay populates brand
   correctly.

**Consequence**: The Brand column now fills for products where eBay
doesn't populate it, so users who want Brand visible can leave it
in `report_columns`. No change for RAM (brand is already populated
by the adapter). Inference is profile-scoped, deterministic, and
fully under user control — no LLM involvement, no risk of
hallucinated brands.

**Trade-offs**:
- Title prefix has to actually contain the brand. Aftermarket
  cables for "Bose 700" would (incorrectly) be assigned brand
  "Bose"; expected to be rare and the existing `title_excludes`
  filter usually catches these.
- MPN suffers the same issue (eBay often doesn't return it) but
  there's no equivalent candidate-list approach because MPNs are
  arbitrary alphanumeric strings. Deferred — see Open follow-ups.

---

## ADR-025 — Per-product report columns via `report_columns:` profile field

**Status**: ACCEPTED

**Context**: The synthesizer's "Ranked listings" table was hardcoded
to 8 columns suited for RAM (`rank, source, title, price_unit,
total_for_target, qty, seller, flags`). When the second product
(Bose NC700) onboarded in continuation 5, several of those columns
were either meaningless (`qty` always shows "unknown" for eBay
headphones) or absent fields the user wanted (`condition`,
`seller_rating`, `ship_from`). Hardcoding a one-size-fits-all
column set against the architectural goal of supporting arbitrary
product types.

**Decision**:

1. **Column registry in `synthesizer.py`**: `COLUMN_DEFS` maps
   stable column ids to `(header, formatter)` tuples. Formatters
   are pure `(rank_index, listing) -> str` functions reading only
   `Listing` model fields. Initial registry has 14 ids:
   `rank, source, title, price_unit, total_for_target, qty,
   condition, brand, mpn, seller, seller_rating, ship_from,
   qvl_status, flags`.
2. **`DEFAULT_REPORT_COLUMNS`**: the legacy 8-column shape. Used
   when a profile leaves `report_columns` unset, preserving
   backwards compat for existing profiles.
3. **Profile schema**: optional `report_columns: list[str]`,
   validated against the registry's allow-list, deduplicated, and
   non-empty if provided. Same allow-list mirrored in
   `web/lib/onboard/schema.ts:KNOWN_REPORT_COLUMNS` for TS-side
   pre-commit validation.
4. **Onboarding prompt update**: an "Available report columns"
   section enumerates the 14 ids with one-line descriptions and
   gives use-case examples (RAM uses default; headphones drop
   `qty`, add `condition`). Interview step 8 asks the user about
   columns. An "Edit mode" section (added in `555b3ad`) ensures
   the AI surfaces the current `report_columns` proactively when
   the chat starts with a pasted existing profile.
5. **No support for `attrs:<key>` columns yet** (e.g.
   `attr:capacity_gb`). Listed as a v2 follow-up — adds ~15 lines
   to the registry but not needed for either of the two live
   products.

**Consequence**: Each profile picks its own table shape. The
synthesizer's deterministic table builder iterates the chosen
columns and never sees raw column ids the LLM might have invented
(the profile validator rejects unknown ids before the run starts).
Post-check is unaffected because the table is injected
deterministically AFTER the LLM output is checked.

**Reversibility**: Removing the field from a profile reverts it to
the default 8-column shape with no other change required.

---

## ADR-024 — Swap synth model from Claude Haiku 4.5 back to GLM 4.5 Flash; commit-on-failure workflow

**Status**: ACCEPTED (supersedes ADR-019's Haiku-as-synth choice)

**Context**: ADR-019 swapped synth from GLM 4.5 Flash to Claude
Haiku 4.5 because GLM was emitting eBay URLs with mangled tracking
parameters that the strict ADR-001 post-check rejected. That choice
was sound at the time. Two things changed since then:

1. **Synthesizer rewrite (2026-04-30, before continuation 1)** moved
   the ranked-listings table and the diff section out of the LLM and
   into deterministic Python (`build_listings_table_md`,
   `build_diff_md` in `synthesizer/synthesizer.py`). The LLM now
   only contributes Bottom line / Flags / Context — no URLs at all.
   The post-check was correspondingly relaxed to validate only
   numbers, not URLs (`post_check` docstring: "URLs are now
   generated programmatically by python, so we only check numbers").
   The verbatim-URL-copy guarantee that ADR-019's swap was protecting
   no longer depends on the synth model — it's enforced structurally.

2. **Haiku 4.5 fabricates numbers on prod-scale data** (~50%
   observed in continuation 3, vs the ~20% on fixtures the ADR-019
   handoff anticipated). Two consecutive runs on
   `ddr5-rdimm-256gb` produced one clean run and one
   `fabricated numbers: ['169.54', '250']` rejection. Fixture-vs-prod
   divergence is a known regime gap (memory:
   project_synth_regime_gap.md).

**Decision**:

1. **Switch `DEFAULT_SYNTH_PROVIDER` back to `glm` and
   `DEFAULT_SYNTH_MODEL` back to `glm-4.5-flash`** in
   `worker/src/product_search/config.py`. Phase 5 benchmark scored
   GLM 4.5 Flash 10/10 on the same post-check at $0/run. The
   simplified prompt (only Bottom line / Flags / Context) is closer
   to the benchmark fixtures than the prior 5-section ask, so the
   benchmark result is more predictive than it was for ADR-019. Env
   vars `LLM_SYNTH_PROVIDER` / `LLM_SYNTH_MODEL` remain the override
   path.

2. **Add `if: always()` to "Commit and Push changes" in both
   `.github/workflows/search-on-demand.yml` and
   `search-scheduled.yml`** so a synth post-check failure no longer
   masks itself behind a stale-cache surface on the web UI.

3. **Write a stub diagnostic report on `PostCheckError`** in
   `cli.py` before `sys.exit(1)`: the error message, fetched/passed
   listing counts, and the sources panel. Without this, `if:
   always()` has no effect on a first-run-of-the-day failure (no
   report file to commit). With it, the user sees the failure mode
   rendered on the web UI.

**Consequence**: Synth cost drops from ~$0.001/run back to $0/run.
The two failure modes that motivated ADR-019 — URL fabrication and
silent stale-cache — are both addressed structurally now (the
deterministic table + the always-commit + stub report) rather than
by paying the Haiku premium for verbatim-copy reliability that the
synth no longer needs.

**Reversibility**: Trivial. Set `LLM_SYNTH_PROVIDER=anthropic` /
`LLM_SYNTH_MODEL=claude-haiku-4-5` in the workflow env block.

**Open follow-up**: Re-run the Phase 5 benchmark fixtures against
both `glm/glm-4.5-flash` and `anthropic/claude-haiku-4-5` with the
post-numbers-only post-check, to formally re-confirm the choice.
Not blocking; live data is the next signal.

---

## ADR-023 — `ai_filter` swaps to Anthropic Claude Haiku 4.5; parser tolerates prose preambles

**Status**: ACCEPTED (supersedes the GLM-4.5-Flash choice in ADR-022)

**Context**: The first run after committing ADR-022's full-rule-defs
fix and the new diagnostic block produced an "AI filter diagnostic"
section in the committed report
(`reports/ddr5-rdimm-256gb/2026-04-30.md`) that showed exactly what
GLM-4.5-Flash was doing: emitting chain-of-thought prose like
"Let me analyze the products one by one according to the rules
provided. First, let's review the rules: 1. form_factor_in
{values:..." despite `response_format=json_object` being set. The
JSON parse failed on the first character. This is the same failure
class we previously attributed to GLM-5.1 being a reasoning model —
turns out GLM-4.5-Flash also dumps prose into `content` for prompts
of this complexity, even though it's nominally non-reasoning.

The 2026-04-30 PROGRESS handoff explicitly anticipated this:
"If GLM 4.5 Flash also failed silently, the next move is to try a
different provider for ai_filter (e.g. anthropic / claude-haiku-4-5)."

**Decision**:

1. **Switch ai_filter to `anthropic / claude-haiku-4-5`**. Haiku 4.5
   has been the synth model since ADR-019 and reliably honors
   "JSON only" prompting. Cost is roughly $0.005/run for ~100
   listings vs near-zero for GLM, but correctness > cost.

2. **Tolerate prose preambles in the JSON parser**. Replace the
   strict `json.loads(raw_text)` with `_extract_json(raw_text)`,
   which first tries the whole string, then walks from the first
   `{` or `[` and uses `JSONDecoder.raw_decode` to extract the
   longest valid JSON value at that position. This is defense in
   depth: even if Haiku occasionally tacks on a prose sentence,
   the run won't zero out. Pinned by a new test
   (`test_tolerates_prose_preamble_before_json`).

**Consequence**: ai_filter has a known-good model behind it.
Future provider/model swaps don't need to also iterate on a
strict-parse-only contract. The strictness/correctness boundary
stays at the synthesizer (where ADR-001's post-check still
forbids fabricated numbers).

---

## ADR-022 — `ai_filter` prompt sends full rule definitions; per-product filter log committed alongside report

**Status**: ACCEPTED (refines, does not supersede, ADR-021)

**Context**: From the 2026-04-30 session, prod ai_filter consistently
returned `ebay_search ok 95 / 0` despite five debug commits switching
models, parser shapes, prompt wording, and adding artifact uploads. The
diagnostic artifact required GH Actions auth to download, so the
operator had no way to see what GLM was actually returning per listing
without manually pulling it.

Root cause was simpler than any of the model/prompt theories: the
filter prompt rendered rules via `[r.rule for r in profile.spec_filters]`,
which extracted only the rule *type name* and dropped every value. The
LLM saw `["form_factor_in", "speed_mts_min", "voltage_eq",
"title_excludes", ...]` with no idea which form factors were allowed,
what the speed minimum was, or which substrings to exclude. The LLM's
honest, conservative response was to fail nearly every listing on
"insufficient information." That was indistinguishable in the report
from a working filter applied to genuinely bad listings.

**Decision**:

1. **Send full rule definitions**: ai_filter dumps `r.model_dump()` per
   rule, so the LLM receives `{"rule": "form_factor_in", "values":
   ["RDIMM", "3DS-RDIMM"]}` instead of just `"form_factor_in"`. The
   prompt also now contains an explainer per rule type (form_factor_in,
   speed_mts_min, ecc_required, voltage_eq, min_quantity_for_target,
   in_stock, single_sku_url, title_excludes), telling the LLM how to
   interpret each rule against `attrs`/`title`/`url`/`quantity_available`
   with a consistent "unknown ≠ failed" semantic.

2. **Per-product filter log committed alongside the report**: each
   ai_filter run also writes to `reports/<slug>/<date>.filter.jsonl`
   (truncating per call), one row per evaluated listing with `index`,
   `pass`, `reason`, `title`, `price`, `url`, `source`. The workflow's
   existing `git add -A` step commits it, so any future 0-pass run is
   debuggable from the public repo without GH Actions auth. The daily
   `worker/data/filter_logs/<date>.jsonl` and the workflow artifact
   upload are unchanged — they remain the authenticated path.

3. **Inline diagnostic block in the markdown report**: when
   `passed_listings == 0` and `all_listings > 0`, the report appends an
   "AI filter diagnostic" section with the first 10 rejection reasons in
   a markdown table (or, on hard call-level failure — JSON parse error,
   unexpected shape, exception — the first 600 chars of the raw LLM
   response, fenced). The user sees the failure mode at a glance on the
   web UI, no log diving required.

**Consequence**: Future ai_filter regressions surface in the committed
report instead of appearing as silent "0 passed" rows. The prompt is
self-describing and no longer relies on the LLM guessing rule
semantics from rule names. Trade-off: the prompt is longer (~1200 more
tokens of rule explanations), but ai_filter is already on
glm-4.5-flash (cheap and non-reasoning), so the marginal cost is
negligible.

---

## ADR-021 — Universal AI Extraction and AI-Aided Filtering

**Status**: ACCEPTED (supersedes the strict "deterministic extraction only" rule from ADR-011)

**Context**: In Phase 12, it became clear that maintaining deterministic scraping code (CSS selectors) for every vendor discovered by the onboarding AI was a significant bottleneck. Small site changes would silently fail the deterministic adapters. Furthermore, the deterministic Python filter pipeline was difficult to adapt to fuzzily described long-tail consumer products.

**Decision**: 
1. **Universal AI Adapter**: We introduced `universal_ai_search` which fetches raw HTML from any given URL and uses GLM-5.1 to extract product listings into a JSON format.
2. **AI-Aided Filtering**: We replaced the deterministic python `apply_filters` function with an `ai_filter` step that asks GLM-5.1 to evaluate all extracted listings against the profile's strict rules, outputting only the indices of valid listings.
3. **Structural Safety Net**: To uphold ADR-001 (no fabricated data), the Universal Adapter enforces that any URL extracted by the LLM MUST be a verbatim substring present in the raw HTML. The valid Python objects are then passed deterministically to the Synthesizer (Haiku) which builds the final report.

**Consequence**: The onboarding AI can now confidently add arbitrary vendor URLs to the `sources` list without requiring a human developer to write a custom adapter. The deterministic filters still exist in code as a fallback or for specialized properties, but the primary filter uses AI reasoning. This greatly accelerates onboarding at the cost of higher LLM token usage during the extraction and filtering phases.

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

**Status**: SUPERSEDED by ADR-034 (model is now `claude-haiku-4-5` with prompt caching + structured-intent JSON; same Anthropic web_search server-tool path).

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
