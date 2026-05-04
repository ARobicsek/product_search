# Session Protocol

The point of this document: a Claude or Gemini dev session starts oriented and ends with the repo in a state the next session can pick up cold. No re-reading the whole repo. No re-debating decided points. No half-finished work.

## At the start of a session

Run this checklist. It takes <2 minutes and saves enormous token waste later.

1. **Read `docs/PROGRESS.md`.** It is the source of truth for: active phase, current task, last commit, blockers. If it disagrees with what you remember, it wins.
2. **Read the active phase's brief in `docs/PHASES.md`.** Only the section for the active phase. Skip the others.
3. **Skim `docs/DECISIONS.md` for decisions tagged with the current phase.** Don't re-debate `STATUS: ACCEPTED` items.
4. **Open the files PROGRESS.md tells you to.** Don't open more than that yet.
5. **Confirm the task.** State it back in one sentence. If anything is ambiguous, ask before coding.

If you are an AI assistant: do not glob the whole repo. Do not read files outside the current phase's listed scope. The phase brief tells you what you need.

## During a session

- **Stay in scope.** If you find yourself wanting to fix something unrelated, write it in `docs/PROGRESS.md` under "noticed but deferred" and move on. The next session can pick it up.
- **Use committed fixtures.** `worker/tests/fixtures/` has saved HTML/JSON for every adapter. Use them in tests. Only hit live sites when adding a new adapter or when a fixture is being captured.
- **Write tests with the code, not after.** A phase isn't done if the test exists but doesn't pass, or if no test exists for code that has one in scope.
- **One phase per session.** If you finish early, stop and hand off. Don't start the next phase — its brief was written assuming a fresh session.
- **Don't push without explicit approval.** Local commits are fine. Pushing publishes work and burns CI budget.

## At the end of a session

In this exact order:

1. **Make sure tests pass and the build is green locally.** No "I'll fix it next session."
2. **Capture fixtures if you scraped anything live.** Save them under `worker/tests/fixtures/<adapter>/<descriptive-name>.html` (or `.json`). Strip any session-specific noise.
3. **Update `docs/PROGRESS.md`:**
   - Mark the current task done.
   - Set the next task explicitly. The next session should not have to think about what to do first.
   - Note any blockers, surprising findings, or "noticed but deferred" items.
4. **Append to `docs/DECISIONS.md`** if anything material was decided this session. One ADR-style entry per decision (Context / Decision / Consequence).
5. **Commit.** One focused commit per phase is fine; multiple small commits within a phase are fine. Commit message format:
   ```
   phase N: <one-line summary>

   <bullet list of what changed and why>
   ```
6. **Push** unless the user explicitly asked not to. 

## How to brief a new session

When opening a fresh Claude or Gemini chat, the entire onboarding prompt should be:

```
Working in product_search repo. Read docs/PROGRESS.md and follow the
session protocol in docs/SESSION_PROTOCOL.md.
```

That's it. Everything else flows from PROGRESS.md.

## When something goes wrong

- **Lost context mid-session?** Re-read PROGRESS.md and the active phase brief. Don't try to reconstruct from memory.
- **Disagreement with a prior decision?** Don't silently override it. Add a new entry to DECISIONS.md proposing a change, flag it in PROGRESS.md, and hand off.
- **Phase is taking longer than expected?** Stop. Reassess the phase brief. If the brief was wrong, update [docs/PHASES.md](PHASES.md), update PROGRESS.md, commit the doc changes, and end the session. The next session starts with a corrected brief.
- **Tests are flaky or fixtures stale?** Fix the fixture, don't disable the test. A flaky test is a regression in this codebase.

## Why this discipline

The architectural premise of the project is that LLMs cannot be trusted with verification work — only with synthesis of pre-verified data. The dev process mirrors the runtime: AI dev sessions are good at executing well-bounded tasks against verified state. They are bad at managing their own context, deciding what's in scope, and remembering yesterday. PROGRESS.md and the phase briefs are the equivalent of the validator pipeline: they keep the AI bounded to the work that's actually next.
