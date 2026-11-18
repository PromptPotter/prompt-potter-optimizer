# l4-runs: the runs L4 pilots lean on report what they did, and survive a landing (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `l4-runs`, arc
`arc/l4-runs`.

- **Base:** `origin/main` at `7d5d12f7b` (the end pass closes the cycle on a stop). Your `<base sha>`.
- **Why:** L4 pilots now run every day beside building chains: an outer bench pass grades the
  adopted selection on held-out seats at every adoption (`every_adoption`), and landings happen
  while pilots run. Each row below is a state a run enters but does not report, or a crash a run
  meets beside a landing.
- **Spec:** `docs/specs/code-debt-cleanup.md` § `wave-5 · l4`, these rows only (re-run each Re-test
  first): "A moved roster point closes the cycle as a crash", "A landing holds every other entry for
  minutes, and they crash on it", "No test runs a detached job", "The offline run refuses outside a
  git clone". Plus two rows that are not in the ledger yet (measured 2026-11-15 on a
  `promptpotter-self/run` C0, outer campaign `promptpotter-self.run__b400fa`):
  - **The bench narrates "pending" after its pass graded.** The readout printed, in this order:
    `bench R0: fitness — on 3 held-out rows`, then `bench: no headline — The pass this reading waits
    on has not finished.` (`domain/paired_reading.py::READING_STATE_INFO[PENDING]`, rendered by
    `views/render/ansi.py:423`), then after the round summary `bench: no headline — No round closed
    past the origin, so no individual is selected to pair.` The first headline is wrong: the pass
    had finished. Root: find which reader takes the bench reading before the pass's graded record is
    folded (`ledger_scan.py::scan_bench_passes`). **Re-test:** a C0 with a bench pass on the offline
    world narrates no PENDING headline after its grading line.
  - **The outer's bench spend reads zero.** The same C0's served dashboard (`dashboard --json`):
    `spend_metered.kinds.bench` = `{counted: true, sent: false, metered_usd: 0.0, tokens: 0}` while
    its bench pass ran 3 inner runs on held-out seats; their spend sits inside `kinds.backend`
    ($0.442 billed for 9 inner runs). The bench's price is what decides how often L4 can afford to
    grade held-out, so it must read on its own kind. Root: where a call's spend kind is decided for a
    bench pass's cells. **Re-test:** a bench pass's calls read under `kinds.bench` on the offline
    world; `kinds.backend` drops by the same amount.
- **Links** (one commit each): (1) the bench pass reports what it did: the moved-point refusal banked
  on the member's pass and read refused by the fold (the cycle keeps its search's stop), the PENDING
  headline, the bench spend kind; (2) the landing hold: time `plan_landing` by phase on a copy of a
  real-sized root (the offline world's, scaled), cut the slow phase, and an entry finding a landing
  in flight prints a wait line, never a traceback; (3) one test drives `run_job` on a loop against a
  real registry (adopt, refuse, release), and `scripts/processes.py::clone_root` stops being a
  precondition of the offline run outside a clone (or names its refusal and the ledger row's
  Action becomes the doc line; pick the root).
- **The code shrinks** where it can; the bench refusal and the spend kind are substance, named.
- **Beside you (local, `arc/l4-outer`):** a chain owns `application/optimizers/potter/**`,
  `assets/optimizers/potter/**`, `application/runner/inner/**`, `domain/l4/**`,
  `application/bench/node_context.py`, `application/bench/llm_call.py`,
  `domain/optimizer_template.py`, `application/pipeline_resolve.py`, `connectors/protocol.py`,
  `research/datasets/promptpotter-self/**`, `scripts/offline_run.py`'s L4 leg and
  `behaviour_keys.json`. A holder of your rows inside those paths is a cut ("rows to file"), never an
  edit. You own the rest of `application/runner/` (bench.py, entry.py), `application/scoring/`,
  `domain/arms.py`, `domain/paired_reading.py`, `infrastructure/store/` (ledger_scan, io, the
  landing's readers), `application/maintenance/landing.py`, `application/jobs/`, the spend
  projections (`infrastructure/projections/live_dashboard.py`, `application/served_dashboard.py`),
  `application/views/`, `scripts/processes.py`, and the tests over these. If the bench spend kind's
  root sits in `runner/inner/**`, cut it with the exact `file:line` and the one-line fix.
- **Regenerated, never hand-edited:** TS types (`scripts/build_ts_types.py`), specs
  (`scripts/build_specs.py`), `behaviour_keys.json` if your links move a key (name old → new).
