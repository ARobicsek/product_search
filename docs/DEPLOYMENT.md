# Deployment

Two deployable units: the worker (GitHub Actions) and the web app (Vercel). No always-on server.

## Worker — GitHub Actions

### Why GitHub Actions

- Free for public repos. Generous free tier for private (2,000 min/month).
- No server to maintain.
- Commit history of every report comes free.
- `workflow_dispatch` gives us on-demand runs without a separate API.

### Workflows

#### `.github/workflows/search-scheduled.yml`

Runs every hour. The job reads each profile's `schedule.cron`, computes whether the product is due *this hour*, and runs only those that are. Single workflow file, fan-out at runtime.

```yaml
on:
  schedule:
    - cron: "0 * * * *"   # top of every hour
  workflow_dispatch: {}    # manual kick of the scheduler

jobs:
  fanout:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ./worker
      - name: Run due products
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          # ... other LLM keys per LLM_STRATEGY.md
          EBAY_CLIENT_ID:    ${{ secrets.EBAY_CLIENT_ID }}
          EBAY_CLIENT_SECRET: ${{ secrets.EBAY_CLIENT_SECRET }}
        run: python -m product_search.cli scheduler-tick
      - name: Commit reports
        run: |
          git config user.email "actions@github.com"
          git config user.name  "product_search bot"
          git add reports/
          git commit -m "scheduled run: $(date -u +%F-%H)" || echo "no changes"
          git push
```

`scheduler-tick` walks `products/`, checks each profile's cron against the current UTC hour using `croniter`, and invokes `search <slug>` for due ones.

#### `.github/workflows/search-on-demand.yml`

Triggered by web UI, or by `gh workflow run`.

```yaml
on:
  workflow_dispatch:
    inputs:
      product:
        description: "Product slug"
        required: true
        type: string

jobs:
  run-one:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -e ./worker
      - env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          # ... etc
        run: python -m product_search.cli search "${{ inputs.product }}"
      - name: Commit report
        run: |
          git config user.email "actions@github.com"
          git config user.name  "product_search bot"
          git add reports/
          git commit -m "on-demand run: ${{ inputs.product }}" || echo "no changes"
          git push
```

#### `.github/workflows/ci.yml`

Runs on every push and PR.

- Lint (ruff).
- Type-check (mypy or pyright).
- Tests against committed fixtures.
- Validate every product profile (`cli validate <slug>`).
- Build the web app.

CI never hits live external sites.

### Secrets

Set these as repository secrets at `Settings → Secrets and variables → Actions`:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `GLM_API_KEY`
- `EBAY_CLIENT_ID`
- `EBAY_CLIENT_SECRET`
- (optional) `SLACK_WEBHOOK_URL`

The bot pushing reports uses the default `GITHUB_TOKEN` — no extra setup.

## Web — Vercel

### Why Vercel for the web app

- Native Next.js host. Zero-config preview deploys per branch.
- Free tier covers our traffic.
- Edge functions for the small API routes the UI needs.

(Netlify is fine too. The plan goes Vercel because Next.js App Router has slightly cleaner DX there. If at any point the team prefers Netlify, switching is mostly a config change — there's nothing Vercel-specific in the app code.)

### Routes

- `/` (RSC) — list products and their latest report bottom line.
- `/[product]` (RSC) — render latest report markdown + history list + "Run now" button (client component).
- `/onboard` — onboarding interview UI (client component, streams from Vercel API route).
- `/api/dispatch` — POST. Triggers `search-on-demand.yml`. Auth: simple shared secret in header (the user is the only user).
- `/api/onboard/chat` — POST. Forwards to the configured onboarding LLM, streams response.
- `/api/onboard/save` — POST. Validates the proposed profile YAML and commits it via the GitHub Contents API.

### How the web reads reports

For a public repo, just `fetch` raw GitHub URLs:

```
https://raw.githubusercontent.com/ARobicsek/product_search/main/reports/<slug>/<date>.md
```

For a private repo, route through `/api/reports` which uses a fine-grained PAT on the server.

ISR: revalidate every 5 minutes is fine. The on-demand "Run now" path manually revalidates the product page once GitHub Actions completes (poll the run status from the client; once `conclusion: success`, call `revalidatePath`).

### Environment variables on Vercel

- `GITHUB_DISPATCH_TOKEN` — fine-grained PAT, scope: `actions: write` on this repo only.
- `GITHUB_REPO` — `ARobicsek/product_search`.
- `LLM_ONBOARD_PROVIDER` and `LLM_ONBOARD_MODEL`.
- The matching LLM API key for the onboarding provider (e.g., `ANTHROPIC_API_KEY`).
- `WEB_SHARED_SECRET` — header-token gate on `/api/dispatch` so randos can't spam runs.

### Mobile-friendly

Tailwind. Test at 375px viewport (iPhone SE width) before shipping any UI work. The report view should be:

- Single-column.
- Tables horizontally scroll; do not let them blow out the layout.
- "Run now" button is thumb-reachable and shows a clear state machine: idle → dispatching → running → done (with timestamp).

## Local development

`worker/` runs locally with:

```
cd worker
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e .[dev]
cp ../.env.example ../.env  # then fill in
python -m product_search.cli search ddr5-rdimm-256gb
```

`web/` runs locally with:

```
cd web
npm install
npm run dev
```

The dev web app can be pointed at the live repo (read reports from GitHub) or at a local checkout (read from disk via `NEXT_PUBLIC_REPORTS_SOURCE=local`).
