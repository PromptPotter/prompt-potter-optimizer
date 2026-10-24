# One served hierarchy — stage 4

> **Scope.** The dashboard read slimmed to run control. Stages 1–3 (campaign, course, round,
> candidate and step entries, `GET /hierarchy`, `GET /hierarchy/{address}` and its `/@` window) have shipped and are a contract now:
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

## Stage 4 — the dashboard read slims to live run control

The masthead chip row, the run card and `run-summary.ts` read a course entry at a moment; the
`ServedDashboard` fields those surfaces read that a course entry carries die; its `RunStatus.of` folds
into `HierarchyState.of`, so the `RunStatus.of` probe becomes `== 2`. Specified in full before it is built.
