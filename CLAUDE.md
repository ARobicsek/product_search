# Claude Code primer

This file is auto-loaded by Claude Code at session start. Keep it small.

## Before doing anything else

1. Read [docs/PROGRESS.md](docs/PROGRESS.md). It tells you the active phase, the current task, and what was just done.
2. Read the active phase's section in [docs/PHASES.md](docs/PHASES.md).
3. Read the relevant decisions from [docs/DECISIONS.md](docs/DECISIONS.md). Don't re-debate decided points.
4. **Then** start work. Do not pre-emptively read other files. Ask before reading anything outside the current phase's scope.

## Session protocol

Full rules: [docs/SESSION_PROTOCOL.md](docs/SESSION_PROTOCOL.md).

Short version:
- One phase per session. Don't span phases.
- Use committed test fixtures in `worker/tests/fixtures/`. Don't re-scrape live sites unless explicitly required.
- Before ending: update `docs/PROGRESS.md`, log new decisions in `docs/DECISIONS.md`, commit. Do not push without explicit approval.

## Hard rules

- The LLM never produces a price, stock count, URL, or quote that the deterministic layer didn't actually fetch. This is the architectural commitment. If you're tempted to ask the LLM to "find listings" or "verify a price," stop and re-read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- `.env` is never committed. Real secrets go in GitHub Actions secrets and Vercel environment variables. The repo only has `.env.example`.
- Do not add abstractions, error handling, or features the current task didn't ask for. Trust the validator pipeline at the seams.
- For the web UI, mobile layout is non-negotiable — test it at narrow viewport before claiming done.
