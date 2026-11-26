# w7-line: one served answer for what runs now, and one served candidate line viewed through masks (cloud · Opus)

`briefs/common.md` and `briefs/w7-shared.md` bind you (read BASE FIRST). Base **`2c7dff34e`**.

SPEC: `docs/specs/roadmap.md` item **"First after v0.8.21: one served answer for what runs now"** (its
three roots) together with these `docs/specs/code-debt-cleanup.md` rows, one arc (operator,
2026-11-26: the live indicator and the candidate-box unification share one root, so each arm's served
row carries its in-flight fact and the client derives nothing): **The candidate box reads one line from
three sources**; **The live dashboard tracks the race beside the narration**; **A Candidates card draws
an empty chart over a served lineage**. Read each row's full text first.

DECIDED (build these; do not reopen):
1. **Active node:** the fold serves one `active: {scope, node}` from the SET of open calls and open
   phases (no single `_in_flight` slot, no last-entered-phase map), null unless the cycle is appending.
   The client reads it as served: `OptimizerCard`'s `isLive && viewingLive`, `NodeDetail`'s re-gate and
   `PipelineStack.tsx`'s unscoped hand-off go; scoped ids never collide under pp-self; the one-node
   layout reads it too.
2. **In-flight arm:** each arm's own served row carries its in-flight fact. `_refresh_open_sample_markers`
   and `measuring_arm` are deleted, so arms racing abreast never flip and a verify pass never names C0.
3. **One line:** the cycle's candidate line is ONE served series, read across its fork chain, the live
   round's arms as rows in the closed rows' shape. It rides the dashboard frame that already serves the
   closed rounds: no new read, no browser stitch of `…/rounds/{n}`. The per-poll re-serve cost is NOT
   yours (the row **The live reads re-serve history per poll** stays, for root D's versions chain).
4. **Masks:** the bars, the dendrogram strip, the chart and the in-flight marks are views of that one
   series. `SubtreeCycleEntryFacts.inherited` and the client's `inflightByKey` join are deleted. The
   masthead's candidate count and the bar count each name which count they show (arms scored over the
   line vs C0 plus every candidate), read from the series, never recounted.
5. **Running per line:** a superseded cycle's dashboard names its successor (a served fact), and
   `OptimizerCard` uses one predicate for its class and its glow.

OWNS: `promptpotter/infrastructure/projections/live_dashboard.py`, `promptpotter/application/served_dashboard.py`,
`promptpotter/domain/dashboard_rows.py`, `promptpotter/application/hierarchy_subtree.py`,
`promptpotter/domain/arm_reading.py` (the in-flight fact only, if it lands there; no θ change),
`webapp/components/{candidates,pipeline,node-surface}/`, `webapp/lib/derivations/{candidate-views,lineage-candidates,live-candidates,run-phase}.ts`
and their `__tests__`, `webapp/lib/hooks/useRunControl.ts`, `webapp/lib/test-fixtures.ts`,
`webapp/e2e/walk/dashboard.spec.ts`, the component CSS of the components you own, `tests/test_archive.py`, plus one block in `tests/test_spend.py`.

LINKS (one commit each): (1) active node; (2) the in-flight fact on the arm row; (3) the one line in
the frame; (4) the masks and the joins deleted; (5) successor + one predicate. Mobile first: the phone
layout is the one that must read right.

LEAN: the browser loses derivations, so `webapp/` should shrink clearly; `promptpotter/` about flat.
Re-tests: `git grep -c "inflightByKey" -- webapp` 0; `git grep -c "inherited" -- promptpotter/application/hierarchy_subtree.py` 0;
`git grep -c "_refresh_open_sample_markers\|measuring_arm" -- promptpotter` 0. Run data: if a served or
recorded shape the run folder holds moves, name the wipe. Report each re-test before and after.
