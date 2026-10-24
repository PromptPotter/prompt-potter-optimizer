# One served hierarchy — stages 3 and 4

> **Scope.** Steps and time lanes on the one hierarchy, then the dashboard read slimmed to run
> control. Stages 1 and 2 (campaign, course, round and candidate entries, `GET /hierarchy` and
> `GET /hierarchy/{address}`) have shipped and are a contract now:
> [`frontend-surface-contract.md`](frontend-surface-contract.md) § The served hierarchy and
> [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § The hierarchy
> address. The arc's record is [`roadmap.md`](roadmap.md) § One served hierarchy. A
> `<!-- target: … -->` is a probe the build arms by renaming it `probe:` in the commit that makes it hold.
>
> **Binding, from the operator (2026-10-24):** one hierarchy, no adapter layer; "eliminate as much code
> as possible"; backbones and modules, zero indirection, no backward compatibility. Dataset, optimizer,
> model, fork, diag and inner are TAGS, never levels. A fork is an EVENT in time: born inside its parent
> at a spawn point and drawn from there. Densities: sidebar row → hover card (the node at its newest
> moment) → run card (the same card, one layer down, at a chosen moment) → compare channel → time lane.
> On-disk data stays READABLE through 2026-10-25; served shapes and code change freely now.

## 1. Stage 3 — step entries, spans, time lanes

`Level += "step"`. A step is one ledger record a surface draws on a time lane: an arm's walk opening
and ending, an election, a close, a fork's cut, an inner spawn — whatever `domain/activity.py::ActivityFeed`
already reads into an `ActivityItem`. Address `{course}/@{offset}`: an offset is the one identity a ledger line has that is immutable and totally ordered — the line index IS `sequence`, kept by every rewrite — and the moment (`?at=`) already speaks it, so a step, a moment and a fork's `spawn.at_offset` share one axis. `HierarchySpan`
gains `opened_offset` / `last_offset` on a course and a round. Steps ride under the course, not the
round: a step of a round's walk and the round's close interleave with another round's in one ledger,
and the round is a `step.facts.round` field, not a parent. The time lane draws a course's steps on its
offsets and a fork at `spawn.at_offset` on its parent's lane. Specified in full before it is built.

## 2. Stage 4 — the dashboard read slims to live run control

The masthead chip row, the run card and `run-summary.ts` read a course entry at a moment; the
`ServedDashboard` fields those surfaces read that a course entry carries die; its `RunStatus.of` folds
into `HierarchyState.of`, so the `RunStatus.of` probe becomes `== 2`. Specified in full before it is built.
