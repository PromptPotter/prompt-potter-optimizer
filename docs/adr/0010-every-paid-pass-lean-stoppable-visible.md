---
status: accepted
date: 2026-10-23
deciders: [maintainer]
consulted: []
informed: []
relates:
  - docs/adr/0003-spend-and-tenancy.md
  - docs/adr/0008-content-is-identity.md
  - application/jobs/CLAUDE.md
supersedes: []
superseded-by: []
tags: [spend, control, measurement, parity]
---

# Every paid pass is lean, stoppable and visible

## Context and Problem Statement

A paid pass is anything that sends cells to a model and waits for them: a campaign round, the
origin, a held-out bench, a `verify`. On 2026-10-23 a bench on `justlogic/d234-held` scored 930
held-out rows per arm, ran for over twenty minutes, could not be stopped by `pause` (which reached
only a campaign loop), and showed nothing on the campaign in the webapp while it ran. The operator
could neither see it nor stop it, and its size bought nothing a short interval needed.

## Decision

1. **No pass scores more than 150 rows.** Bench, verify, origin and round alike. A short confidence
   band is the purpose of a pass; rows past it are spend and clock with no decision behind them. A
   definition or a request asking for more is refused, never silently cut.
2. **Every paid pass is stoppable by the one stop**, the same command that pauses a campaign, from
   every entry point: CLI (and Ctrl+C on the verb's own process), REST, webapp, the embedded launch
   and the chat. A pass only a process kill can stop is a defect.
3. **A stopped or shrunk pass re-pays nothing it already measured.** Its scored cells stay in the
   archive and replay by content ([ADR-0008](0008-content-is-identity.md)); cutting a set to a
   prefix of what was measured replays the prefix.
4. **A pass in flight is visible where the operator watches**: on the campaign, served by the
   backend, whichever entry point launched it. Knowing it only on `machine-status` is not visible.

## Consequences

- The ceiling is one value, `domain/scoring.py::MAX_PASS_ROWS`: it bounds every pass-size knob, the
  verify default and a request's count, and `run_walks` refuses any walk past it before a cell is
  sent. A dataset whose bench split exceeds it is cut at its definition, never at the call site
  (`justlogic/d234-held` was cut 930 → 150 the day this was decided).
- A scoring pass polls the same run control the loop polls, and a stop lands as `pass_stopped` on
  the ledger with what it banked.
- The candidates chart carries verify and bench marks, in flight and done, from one served model.

## Rejected

- **A per-call `--samples` cap alone.** It leaves every default and every definition free to ask
  for more, and the operator finds out after paying.
- **A separate cancel verb for passes.** A second stop is a stop someone forgets to wire.
