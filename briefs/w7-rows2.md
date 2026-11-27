# w7-rows2: the dashboard's and the gate's small rows, each as its Door states (cloud · Sonnet)

`briefs/common.md` and `briefs/w7-shared.md` bind you, with ONE override to BASE FIRST: your base is
**`origin/land/w7`'s tip** (main `2c7dff34e` plus the wave-7 landings, w7-rows the last). It is not on
main yet, so skip the is-ancestor check and report `git log -1 origin/land/w7`.

SPEC: `docs/specs/code-debt-cleanup.md` § wave-7. Close these rows, one commit each, each built as its
**Door** or **Root** line states. Read each row whole first. If a link turns out to need a design the
row does not decide, stop that link, report it, and go on to the next.
1. **The masthead's candidate count names no count**: a served count beside `ServedDashboard.line`,
   named for what it counts; the masthead reads it and derives none.
2. **A superseded cycle's successor is worded twice**: one `Said` on `RunState`, read by both
   `domain/phases.py`'s `start_refusal` and `ServedDashboard.successor`.
3. **A served discriminator no door reads by name**: `WarmingDashboard.warming_up` becomes a `Literal`
   tag the union reads, or goes.
4. **`cycle_reads.py` copies the live round's candidates into a second shape**: its reader takes the
   served line.
5. **`ActiveNode.scope` is never `target`**: under pp-self the outer fold serves the inner run's active
   node as `target` (it already folds `record.inner`). If the inner record does not carry the node,
   take the row's other door instead: the `scope` variant and the client's target branch go.
6. `commands/operator_acts.py`'s `set-coupon` takes `usd` and `tokens` as strings (`-` = unbounded)
   and parses them in the handler. Root: the payload carries a typed unbounded value. File it as a row
   in your ledger commit, then close it.
7. **`walk-world`'s cache touch raises on a first run**: find why a green walk declares a product it
   did not build under `.gate_cache/walk-world/<key>/…`, and fix that. NEVER make the directory before
   the touch: that fakes a cached world for playwright. **Re-test:** a cold `gate.py --only walk-world`
   is green the first time.

OWNS: `application/served_dashboard.py` (the fields above only), `infrastructure/projections/
live_dashboard.py`, `domain/phases.py`, `application/cycle_reads.py`, `commands/operator_acts.py` and
its payloads, `scripts/gate.py`, the webapp components that read each, and their tests. Shared and
named: w7-spend (on `land/w7-wipe`) changes the spend fields of `served_dashboard.py`, so touch no spend
field. Not yours: `jobs/launcher/**`, `commands/launching.py`, `runner/run_init.py` (w7-open2);
`pipeline_resolve.py`, `initialization/**`, `runner/output.py`, `run_observers.py` (w7-res2).

LEAN: `promptpotter/` shrinks or holds, apart from the one served count. Digests, behaviour keys and
equals-main are unchanged; if one moves, stop that link and report it. Run data: none.
