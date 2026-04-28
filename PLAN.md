# Master Plan: product_search

This is the single document the project owner should read end-to-end. Everything else in `docs/` expands one section of this plan.

## 1. Goal

A reliable, auditable, scheduled tracker that finds the best available price for any specified product across many sources. First product is DDR5 RDIMM ECC RAM (256 GB target) — but the architecture is generic from day one.

Five capabilities the user explicitly asked for:

1. **Generalize past RAM** — track any product via a config profile.
2. **Onboard new products by interview** — when a new product is requested, the system asks the user the questions it needs to do a good job, and saves the answers as a profile.
3. **Run on schedule and on demand** — daily, every 6h, or "run now" from the UI.
4. **Mobile web UI** — installable PWA, viewable from a phone, with iOS push alerts on material changes (price drops, new entrants).
5. **Cheapest LLM that does a good job** — multi-vendor (Anthropic, OpenAI, Gemini, GLM) with a benchmarking harness.

## 2. Core architectural commitment

The LLM is downstream of verified data. It never produces a price, stock count, URL, or quote that the deterministic layer didn't actually fetch. This is the entire reason the system works where naive "ask the chatbot" approaches don't. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 3. System shape

```
                    ┌────────────────────────┐
                    │  Web UI (Vercel)       │
                    │  - view latest report  │
                    │  - trigger on-demand   │
                    │  - onboard new product │
                    └─────────┬──────────────┘
                              │ GitHub API (workflow_dispatch)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Worker (GitHub Actions, Python)                                │
│                                                                 │
│  product profile ──▶ adapters ──▶ validator ──▶ storage ──▶ LLM │
│  (YAML)              (per-source) (filter/flag) (SQLite +CSV)   │
│                                                                 │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
                          reports/<product>/<date>.md
                          (committed to repo, web reads them)
```

Two deployable units only:
- **Worker** runs in GitHub Actions on cron and `workflow_dispatch`. Writes reports back to the repo.
- **Web** is a Next.js app on Vercel. It reads reports from the repo via GitHub's raw content URLs (or a small API route) and triggers worker runs.

No always-on server. No database hosting. The repo is the database for this MVP.

## 4. Generalization model: product profiles

Every product the system tracks lives in `products/<slug>/profile.yaml`. A profile encodes:

- **What you want** — target capacity/quantity, valid configurations to reach the target.
- **What counts as compatible** — hard-reject filters and soft-warning flags.
- **Where to look** — which source adapters to invoke and with what queries.
- **Domain notes** — reference data (e.g. QVL list) and synthesis hints (e.g. the bandwidth tradeoff note for the EPYC 9224 case).
- **Schedule** — cron expression(s).

A single template lives at [products/_template/profile.yaml](products/_template/profile.yaml). The first concrete profile is [products/ddr5-rdimm-256gb/profile.yaml](products/ddr5-rdimm-256gb/profile.yaml).

Adapters are written generically. They take query parameters, not hardcoded RAM-specific logic. RAM specifics live entirely in the profile and in the validator's per-product hooks.

## 5. New-product onboarding

When the user wants to track a new product, the web UI runs an LLM-driven interview:

1. **Identify** — "What product are you buying? What's a one-line description of the use case?"
2. **Target** — "How much do you need? Any valid alternative quantities?"
3. **Compatibility** — "What are hard requirements? What's a deal-breaker?"
4. **Risk profile** — "Used acceptable? International shipping acceptable? Generic brands acceptable?"
5. **Sources** — "Any sellers you trust or want to avoid?"
6. **Reference data** — "Is there a manufacturer compatibility list (QVL, HCL) you'd like loaded?"

The LLM produces a draft `profile.yaml`. The user reviews it in the UI before commit. See [docs/PRODUCT_ONBOARDING.md](docs/PRODUCT_ONBOARDING.md).

## 6. Scheduling

GitHub Actions handles both modes:

- **Scheduled** — `.github/workflows/search-scheduled.yml` runs on cron. The cron expression is read from each product profile so different products can run on different cadences.
- **On-demand** — `.github/workflows/search-on-demand.yml` triggers via `workflow_dispatch` with the product slug as input. The web UI calls the GitHub REST API to dispatch this.

GitHub Actions has no fee for public repos and a generous free tier for private. No infra to manage.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## 7. LLM cost strategy

Three LLM call sites in this system, each with different requirements:

| Call site | What it does | Capability needed | Default model |
|---|---|---|---|
| Synthesizer | Turn verified JSON into markdown report | Follow instructions, no fabrication | Cheapest tier (Haiku 4.5 / Gemini Flash / GLM Air / GPT-4o-mini) |
| Onboarding interview | Multi-turn product spec extraction | Conversation + structured output | Mid tier (Sonnet / Gemini Pro / GLM-4.5) |
| Adapter helpers | Optionally: parse listings the deterministic layer can't | Vision/structured extraction | Mid tier, sparingly |

All four vendors in `.env` are wired through one `call_llm(provider, model, ...)` abstraction. A small benchmark harness in `worker/benchmark/` runs the same task across configured models with cached input fixtures and reports cost + accuracy. We pick the cheapest model that passes a fixed test bar — and re-run the benchmark whenever models change.

See [docs/LLM_STRATEGY.md](docs/LLM_STRATEGY.md).

## 8. Repo layout

```
product_search/
├── README.md
├── PLAN.md                     # this file
├── CLAUDE.md                   # auto-loaded primer for Claude Code sessions
├── .env.example
├── .gitignore
├── docs/
│   ├── SESSION_PROTOCOL.md     # how AI dev sessions start and end
│   ├── ARCHITECTURE.md
│   ├── PRODUCT_ONBOARDING.md
│   ├── LLM_STRATEGY.md
│   ├── DEPLOYMENT.md
│   ├── PHASES.md               # per-phase briefs with acceptance criteria
│   ├── PROGRESS.md             # LIVE: current phase, blockers, next task
│   └── DECISIONS.md            # LIVE: ADR-style decision log
├── worker/                     # Python; runs in GitHub Actions
│   ├── pyproject.toml
│   ├── src/product_search/
│   │   ├── models.py
│   │   ├── adapters/
│   │   ├── validators/
│   │   ├── storage/
│   │   ├── synthesizer/
│   │   ├── llm/                # provider abstraction
│   │   └── cli.py
│   ├── tests/
│   │   └── fixtures/           # saved HTML/JSON per source — never re-fetched in tests
│   └── benchmark/
├── web/                        # Next.js app; deploys to Vercel
│   ├── package.json
│   └── src/
├── products/
│   ├── _template/profile.yaml
│   └── ddr5-rdimm-256gb/
│       ├── profile.yaml
│       └── qvl.yaml
├── reports/                    # committed; one folder per product
│   └── ddr5-rdimm-256gb/
│       └── 2026-04-29.md
└── .github/workflows/
    ├── search-scheduled.yml
    ├── search-on-demand.yml
    └── ci.yml
```

## 9. Phases

Sized so one dev session (~30-90 min with an AI co-pilot) ≈ one phase. Full briefs in [docs/PHASES.md](docs/PHASES.md).

| # | Phase | Output |
|---|---|---|
| 0 | Bootstrap repo, push to GitHub, confirm decisions | First commit, CI green |
| 1 | Profile schema + DDR5 profile + template | `products/_template/profile.yaml` validates |
| 2 | Python worker skeleton + LLM abstraction + first adapter (eBay API) | `worker/cli.py search ddr5-rdimm-256gb` prints listings |
| 3 | Validator pipeline + flags + QVL annotation | Filtered listings only, with flags |
| 4 | Storage (SQLite + CSV) + diff vs yesterday | Two consecutive runs produce a diff |
| 5 | Synthesizer + multi-vendor benchmark | Cheapest passing model selected; daily report committed |
| 6 | More adapters: NEMIX, CloudStorageCorp, Mem-Store | 20+ listings per run |
| 7 | GitHub Actions cron + workflow_dispatch | Scheduled and on-demand both work |
| 8 | Web UI MVP + PWA shell: list reports, view a report, installable to iOS Home Screen | Vercel deploy URL is live and installable |
| 9 | Web UI: trigger on-demand run, show status | "Run now" button works end-to-end |
| 10 | Web UI: product onboarding interview → committed profile | New product can be added without touching files |
| 11 | iOS push notifications for material changes | Installed PWA gets a push on price drops |
| 12 | Polish: history charts, second product (e.g. GPU) end-to-end | Generality proven |

## 10. Session management discipline

The biggest risk for less-experienced devs working with Claude/Gemini is **context burn** — sessions that re-read the whole repo, re-debate decided points, and run out of tokens before producing useful work. The mitigation is structural:

- **PROGRESS.md** is the entry point. It names the active phase, the current task, the last commit, blockers. Every session reads this first.
- **CLAUDE.md** at repo root tells Claude Code: load PROGRESS.md, load the active phase brief, then ask before reading anything else.
- **One phase = one session.** Phase briefs in [docs/PHASES.md](docs/PHASES.md) are scoped to fit. If a phase looks too big, split it before starting.
- **Test fixtures are committed.** Saved HTML/JSON in `worker/tests/fixtures/` means dev sessions never need to re-scrape live sites to debug.
- **Decisions are written down.** [docs/DECISIONS.md](docs/DECISIONS.md) prevents re-litigating choices ("why httpx not requests?").
- **End-of-session ritual.** Before closing a session: update PROGRESS.md, append to DECISIONS.md if anything new, save fresh fixtures if any were captured, commit.

Full protocol in [docs/SESSION_PROTOCOL.md](docs/SESSION_PROTOCOL.md).

## 11. Open decisions for Day 1

The project owner should confirm or override these before Phase 1 starts. They're in [docs/DECISIONS.md](docs/DECISIONS.md) marked `STATUS: PROPOSED`:

- **Web framework:** Next.js (App Router) on Vercel. Alternative: plain HTML+HTMX deployable to Netlify. Recommendation: Next.js.
- **Worker hosting:** GitHub Actions only (no separate worker service). Alternative: Render/Fly free tier. Recommendation: GH Actions.
- **Database:** None — SQLite committed as artifact + reports in repo. Alternative: Supabase. Recommendation: none, until generality forces otherwise.
- **eBay access:** Browse API (free tier, requires registration). Alternative: HTML scraping. Recommendation: API.
- **Default cheap LLM for synthesis:** Determined by Phase 5 benchmark. Initial guess: GLM-4.5-Air or Gemini 2.0 Flash.
- **Public vs private repo:** Owner's call. Plan assumes public (free GH Actions, no secrets in commits — `.env` is gitignored, secrets injected as GH Actions secrets).
