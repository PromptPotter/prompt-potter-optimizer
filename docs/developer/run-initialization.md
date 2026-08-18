# Run initialization

What happens between `python -m promptpotter new|resume` and the first round — the phase the operator
sees as `INIT` (terminal `✓ Initialized`; webapp "campaign is initialising"). Four functions in
`application/initialization/`, in order, **each step's preconditions being the prior step's
postconditions** — calling them out of order leaves the session under-wired.

1. **`init_services`** (`wiring.py`) — identity → `build_stores` → connector resolve → `BackendClient`
   → `GET /pipeline` merged with the dataset overlay → register backend → `Session` → load dataset.
   *Postcondition:* session has store + client + schema + samples. No scoring, no cycle.
2. **`populate_session_scoring`** (`loop_start.py`) — compile the scorer and round-scorer from the
   formula, wire `state.obs` and `source`. *Postcondition:* `session.scoring` fully populated.
3. **`init_cycle`** (`loop_start.py`) — resolve `cycle_id` (override or content hash), load or create,
   rewind on `--from N`. *Postcondition:* the cycle exists in the store and `resumed_from_round` names
   the next L1 round.
4. **`init_optimization_loop`** (`loop_start.py`) — score the origin, build `Cycle`, mint a sibling on
   fork-on-divergence, emit `INIT.exit`, enter `run_round_loop`.

## The line between init and runner

**Init writes to `Session`. The runner reads `Session` and the `CycleEventLog`**, and never writes back
to `Session` outside the per-round state bundle (`session.state`).

State that must survive across `new` / `resume` goes through the ledger or its projections, never
`Session`; state that is per-process per-cycle lives on `Session.state` or `Cycle`. The separation fails
loud — state that skips the ledger does not survive a restart, visible immediately — so there is no
standing test ([`../../tests/CLAUDE.md`](../../tests/CLAUDE.md)).

## When the chain breaks

- **`init_services` raises `pipeline_config_invalid` (422)** — the dataset's `pipeline.yaml` declares no
  `backend_type`, or neither it nor the backend yields a parseable schema. **Typed rather than bare
  because every measurement is keyed on that schema**, so there is no degraded run to fall through to —
  and a 500 here reads as `transient` to the webapp, which would retry a config only the operator can fix.
- **`populate_session_scoring` raises** — bad scoring formula in `campaign.json`; the diagnostic is in
  the exception. Formula DSL: [`stable-api.md`](stable-api.md) §2.
- **`init_cycle` returns `(None, 1)`** — silent fallback when `session.backend_id` is empty. The runner
  then runs with no per-cycle persistence, which is surprising under `new` / `resume`.
- **`init_optimization_loop` fails origin scoring** — the backend rejects the origin `pipeline_params`.
  Check `datasets/{name}/pipeline.yaml::nodes.{name}.config`.
