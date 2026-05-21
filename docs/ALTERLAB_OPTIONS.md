# AlterLab options — capability audit (Phase 21 / R1)

Source of truth for what AlterLab's scrape API actually accepts and what each
knob costs/does. Written 2026-05-21 from (a) the public docs at
`https://alterlab.io/docs/api/rest` and (b) **empirical probes** against the
live API with our key, because the docs and our long-standing production code
disagree (see "The legacy vs documented schism" below). When they conflict,
the empirical result wins — record it here.

Endpoint: `POST https://api.alterlab.io/api/v1/scrape`, header `X-API-Key: <key>`.
Response: `{"status_code": <origin>, "content": {"html": "..."} | "...", ...}`
or, for a slow render, `202 {"job_id": ...}` which must be polled at
`GET /api/v1/jobs/<id>` until `status` is `completed`/`failed`.

## The legacy vs documented schism (READ THIS FIRST)

Our production adapter (`worker/.../adapters/universal_ai.py`) has always sent a
**legacy/undocumented** body shape:

```jsonc
{ "url", "sync": true, "formats": ["html"], "asp": true,
  "country": "us", "min_tier": 3, "advanced": { "render_js": true, "wait_for": 5 } }
```

The **current public docs** describe a different shape — no `asp`, no top-level
`country`, no `min_tier`, no `wait_for`:

```jsonc
{ "url", "mode": "auto", "sync": true, "formats": ["html"],
  "location": { "country": "us" },
  "cost_controls": { "max_tier": "4", "prefer_cost": true },
  "advanced": { "render_js": true, "wait_condition": "networkidle" } }
```

The deployed API still **accepts** the legacy fields (ADR-070 proved `asp:true`
materially changes the render), so it has back-compat aliases. But empirically
the legacy shape is far less reliable than the documented one — see R2.

## Parameter reference

| Knob | Where | Type / values | Notes |
|---|---|---|---|
| `url` | top | string | required |
| `sync` | top | bool (default true) | true → poll internally, return 200; **but for a slow/heavy render the API still falls back to a `202` job** that we must poll. This 202 path is the dominant failure mode (see below). |
| `formats` | top | `["html"]` | makes `content` an object with `.html` |
| `asp` | top (legacy) | bool | anti-scraping/anti-bot bypass. Undocumented but **works** and matters (ADR-070). Keep sending it. |
| `country` | top (legacy) | ISO-2 | exit-proxy country. Documented equivalent: `location.country`. |
| `min_tier` | top (legacy) | int 1..4 | minimum fetch tier. **`min_tier:4` is harmful** — it forces the synchronous browser tier, which AlterLab queues as a `202` job that does **not** complete within a 120 s poll → body 0 (R2: Target 0/3, B&H 0/3 at tier 4). Do NOT escalate via `min_tier`. |
| `wait_for` | `advanced` (legacy) | — | **NOT A REAL PARAMETER. Do not send it.** Sending `wait_for` (int *or* string) pushes the request into the async `202` job queue that never resolves → body 0. This was the B&H/Target body-0 bug. Migrated to `wait_condition` (ADR-071). |
| `wait_condition` | `advanced` | `domcontentloaded` \| `networkidle` \| `load` | the real "wait for the page to settle" knob. `networkidle` for async-rendered prices. Returns sync 200 (does NOT 202-hang like `wait_for`). |
| `render_js` | `advanced` | bool | headless-browser JS render (+credits). |
| `mode` | top (doc) | `auto`\|`html`\|`js`\|`pdf`\|`ocr` | `auto` lets AlterLab pick + escalate tiers. |
| `location.country` / `.language` | top (doc) | ISO-2 / ISO-1 | documented geo knob (proxy country + Accept-Language). |
| `cost_controls.force_tier` | top (doc) | "1".."4" / "3.5" | pin one tier (no escalation). |
| `cost_controls.max_tier` | top (doc) | "1".."4" | escalate UP TO this tier, starting cheap, returning a **fast sync 200** — the reliable way to "use tier 4 if needed" (unlike legacy `min_tier:4`). |
| `cost_controls.prefer_cost`/`prefer_speed`/`fail_fast` | top (doc) | bool | tier-selection strategy. |
| `cache` | top | bool | **LEAVE DEFAULT (do NOT send `cache:false`).** R2 proved `cache:false` is *harmful*: it forces a fresh render every time, which 202-hangs (documented shape went 3/3 → **0/3** when `cache:false` was added). With caching left on, AlterLab replays a *good* prior render in ~2 s. The earlier "cached bad body" observation was the **legacy** shape caching its *own* bad render; the fix is to produce good renders (documented shape), not to disable cache. |
| `timeout` | top | int 1..300 (default 90) | per-request seconds. Our poll budget for a 202 job is the bottleneck. |
| `screenshot`/`generate_pdf`/`ocr` | `advanced` | bool | unused here (cost). |

### Tier cost ladder (per the docs)

- Tier 1 (curl) $0.0002 — static only
- Tier 2 (httpx + TLS) $0.0003
- Tier 3 (curl_cffi / Chrome impersonation) $0.002 — our default for hard sites
- Tier 4 (Playwright browser) $0.004
- Tier 5 (captcha solve) $0.02

## Empirical findings (R1 matrix + R2, 2026-05-21)

Target WH-1000XM5 **detail** URL (`/p/...A-86777236`), price oracle `$249.99`:

- Legacy base (`asp,country:us,min_tier:3`): **0/3** — two runs `202`→120 s timeout→body 0, one run a cached 393 KB "temporary issue" challenge stub.
- Legacy + `wait_condition:networkidle` (tier 3): **0/3** — same 202-hang / cached stub.
- Legacy + `min_tier:4` + networkidle: **0/3** — *every* run `202`→timeout→body 0 (tier-4 is worse).
- `wait_for:5` / `wait_for:"5"`: `202`→never completes→body 0 (the original bug).
- **Documented shape** (`location.country`, `cost_controls.max_tier:"4"`, `wait_condition:networkidle`, `asp:true`, default cache): **3/3** — 200, 1.34–1.54 MB, `$249.99` every time (one was a 2 s cache hit of the *good* render). This is the only variant that worked, and it worked every time.
- Documented shape **+ `cache:false`**: **0/3** (all 202-hang) — confirms `cache:false` is harmful.

B&H Silver **detail** URL: legacy base **1/3** (one clean 376 KB, two 31 KB challenges incl. a 0.6 s cache hit); legacy `min_tier:4` **0/3** (all 202-hang). B&H search is consistently a 31 KB Cloudflare challenge even with asp+tier3. (Documented-shape B&H replication was cut short when the session was wrapped — re-run next session to decide whether the documented shape rescues B&H detail or it stays walled per T6.)

### Conclusions that drive Phase 21 / ADR-071

1. **Never send `wait_for`** — schema-validated out, migrated to `wait_condition` (T1).
2. **Do NOT escalate by `min_tier:4`** — it triggers the 202-hang. If we escalate tiers, do it via documented `cost_controls.max_tier` (fast sync 200), not legacy `min_tier`.
3. **Send `cache:false`** for price fetches — we always want a live render, and AlterLab replays cached challenge pages otherwise.
4. The **202-async-hang** (heavy render queued as a job that outlives our poll) is the dominant failure; a longer/again-polled fetch or the documented `cost_controls` path is the lever, not stronger proxies.
5. Target detail and B&H are genuinely flaky per-fetch → the retry-on-weak-render loop (T2) plus multiple URLs per vendor (T4) is the systemic mitigation; B&H search stays walled (T6).
