# w11-plugin: the optimizer plugin contract leaves `optimizers/` (areas 09 + 03's edges · cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 09 and § 03, and the debt doc's `## 09` section. §0 says the bench imports no optimizer, yet 13 modules
outside `optimizers/` import `optimizers/nodes.py` (854 lines, 64 commits). That file holds the member
protocols, the round types, the review readings, the knob types and `BehaviourKeys`.

Links, deletion first:
1. **The contract moves below the bench** (decision 1). What a member, a round type, a knob type or a
   behaviour key IS lives in one module the bench and the optimizers both import; `optimizers/` keeps
   only the optimizers. Re-point all 13 importers (the bench, runner, scoring and config edges included:
   import lines only outside your Owns). Name the new home under "decided in-run".
2. **Potter's layers keep a second member protocol**: `class EscalationLayer` goes, as the one protocol
   above.
3. **The upward imports** `scoring/passes.py` and `evidence/head_to_head.py` → `runner/bench.py`, and
   `jobs/launcher/run_job.py` → `runner/entry.py`: close each one the contract's move makes trivial; file
   the rest under area 03.
4. **The three peers' census**: `capo/members.py`, `levi/members.py`, `gepa/members.py`, plus potter's
   `dispatch/facade.py`, `validators.py`, `dispatch/bundle.py`, `dispatch/layout.py`, `knobs.py`,
   `dispatch/injections/layer_state.py`, `l1.py`, `behavior_checks.py`, `optimizer_manifest.py`,
   `potter/members.py` and `dispatch/injections/panels.py`. Does each peer state its paper's rule once?
   Decision 21: CAPO's survivors are ranked on the reference's reading. Fix it if the census confirms the
   row, under its key move.
5. **`locus_crossover` re-crosses a measured or hosted child**, and **an over-budget crossover child books
   its verdict under axis `variant`**: re-test both, then fix along their root. Add the offline leg that
   reaches CAPO's `locked_in` path.

Held: the "slot" prompt comparison and the `l1_critique` proof both need paid runs, so file their
re-tests only. The three recombination routes, `halt_at_fitness` and `block_access` stay (RM319).

OWNS: `application/optimizers/**`, `application/optimizer_manifest.py`, `domain/optimizer_state.py`,
`domain/optimizer_template.py`, `domain/pool.py`, and the contract's new module.

DONE WHEN: `git grep -n "^from promptpotter.application.optimizers" -- 'promptpotter/*.py'
':!promptpotter/application/optimizers'` lists only the registry's loader · `git grep -n "class
EscalationLayer" -- promptpotter` is empty · a hosted recombination keeps its id · each peer's verdict is
in the report · keys: name each move by its mechanism.
