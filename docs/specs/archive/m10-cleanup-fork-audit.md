# M10 cleanup — resume + fork-on-divergence footprint (§3.6)

**Audit scope:** every file that touched `Decision*`, `inherit_from`,
`parent_cycle_id`, `forks/`, `--fork-on-divergence`, `--from`, or the
divergence walker before §3.6 consolidation. Map produced as the
input to the bundling work; final state recorded after the
consolidation landed.

## Pre-§3.6 footprint (16 files)

Largest hotspots before the move:

- `application/optimization/cycle.py` (788 lines) — Cycle + decision
  replayers + 5 REPLAYERS + fork sibling factories +
  `resume_with_divergence_check`.
- `infrastructure/store/campaign_store.py` (628 lines) — fork ledger
  setup + parent_cycle_id metadata plumbing + `rewind_to_round`.
- `tests/test_rescore_and_fork.py` (576 lines) — regression net
  covering all fork paths.
- `presentation/cli/campaign_runner.py` (721 lines) — CLI dispatch
  for `--fork-on-divergence` and `--from`.

| File | Resume/fork footprint (pre-§3.6) |
|---|---|
| `domain/run_records.py` | `DecisionKind` (7 members), `DECISION_GATING`, `GatingMode`, `DecisionRecord`, `record_decision()` sink |
| `application/optimization/cycle.py` | `Divergence`, `ReplayContext`, `ForkResult`, `replay_decisions`, 5 REPLAYERS + `_replay_*` helpers, `_fork_sibling_setup`, `_fork_at_divergence`, `_next_diag_sibling_id`, `fork_for_diag_sibling`, `fork_for_sweep_sibling`, `resume_with_divergence_check` |
| `domain/cycle_paths.py` | `CycleDir` / `RootCycleDir` newtypes; sibling-name regex parsers |
| `infrastructure/store/paths.py` | `root_cycle_id`, `sibling_kind`, `campaign_dir_for` router, `sweep_batch_dir_for` |
| `infrastructure/store/campaign_store.py` | `save_divergence_fork`, `save_diag_fork`, `save_sweep_fork`, `_fork_sibling_metadata_template`, `rewind_to_round`, `prune_dead_forks`, `copy_parent_rounds_and_candidates`, `parent_cycle_id` / `fork_kind` / `forked_from_round` fields |
| `infrastructure/ledger.py` | `CycleLedger.inherit_from(parent, offset)` |
| `presentation/cli/parsers.py` | `--from ROUND`, `--no-divergence-check`, `--fork-on-divergence` flags + help text |
| `presentation/cli/campaign_runner.py` | `cmd_optimize` dispatch into `runner.optimize_cycle` with fork+resume params; `_DIVERGENCE_HINT` derived from `DECISION_GATING` |
| `application/runner.py` | `optimize_cycle` master entry; calls `resume_with_divergence_check` on the fork-on-divergence path |
| `application/optimization/observers.py` | `record_decision` passthrough + projection-subscribe wiring |
| `presentation/api.py` | Read-only ledger tail; surfaces `DecisionRecord`s on the cycle-snapshot endpoints |
| `application/sweep/sweep_runner.py` | `fork_for_sweep_sibling` consumer |
| `tests/test_rescore_and_fork.py` | Regression net: rescore + replay + 3 fork variants + rewind |
| `tests/test_decision_kinds_registry.py` | `DECISION_GATING` exhaustiveness; replayed-vs-archival pairing |
| `tests/test_invariants.py` | `test_no_unexpected_runtime_layer_violations` (hexagonal layers, includes resume+fork modules) |

`RoundResult.deprecated` (the fatal-warning sample lifecycle counter)
is **not** part of the resume+fork bundle — it counts discarded
samples per round, parallel concern.

## Post-§3.6 layout (this commit)

New module: `application/optimization/resume_and_fork/`

| File | Contents (moved or new) |
|---|---|
| `__init__.py` | Public surface: `replay_decisions`, `resume_with_divergence_check`, `fork_at_divergence`, `fork_for_diag_sibling`, `fork_for_sweep_sibling`, plus the re-exported `DecisionKind` / `DecisionRecord` / `DECISION_GATING` / `GatingMode` / `record_decision` / `REPLAYERS` / `Divergence` / `ReplayContext` / `ForkResult` / `Replayer`. |
| `decisions.py` | Resume-checkpoint policy: `GatingMode`, `DECISION_GATING`, `record_decision`, the import-time exhaustiveness check. Re-exports `DecisionKind` and `DecisionRecord` from `domain/run_records.py` (the data shape stays in domain — `CycleRecord` discriminated union owns it). |
| `replayers.py` | Decision replayers: `Divergence`, `ReplayContext`, `Replayer`, `replay_decisions`, the 5 `_replay_*` helpers + `REPLAYERS` registry, the import-time check that every `REPLAYED` kind has a registered replayer. |
| `fork_siblings.py` | `ForkResult`, `_fork_sibling_setup`, `fork_at_divergence` (formerly `_fork_at_divergence`; lost its leading underscore now that it's a public function within the resume_and_fork module), `_next_diag_sibling_id`, `fork_for_diag_sibling`, `fork_for_sweep_sibling`. |
| `resume.py` | `resume_with_divergence_check` only. |

What stays in place (correctly scoped already):

- `domain/cycle_paths.py` — newtypes (data-shape, not policy).
- `infrastructure/store/paths.py` — sibling routing.
- `infrastructure/store/campaign_store.py` — fork metadata writers.
- `infrastructure/ledger.py` — `inherit_from`.
- `domain/run_records.py` — `DecisionKind`, `DecisionRecord`
  (CycleRecord union).

What `cycle.py` shrinks to (after §3.6):

- `Cycle` dataclass + `TrackingState` dataclass.
- `_build_scoreboard` helper (round-trial projection).
- `_rf_dedup_key` (runtime-failure dedup key).
- ~313 lines (down from 788).

## Import migration map

Pre-§3.6:

```python
from promptpotter.application.optimization.cycle import (
    REPLAYERS,
    Divergence,
    ForkResult,
    ReplayContext,
    _fork_at_divergence,
    fork_for_diag_sibling,
    fork_for_sweep_sibling,
    replay_decisions,
    resume_with_divergence_check,
)
from promptpotter.domain.run_records import (
    DECISION_GATING,
    DecisionKind,
    GatingMode,
    record_decision,
)
```

Post-§3.6:

```python
from promptpotter.application.optimization.resume_and_fork import (
    DECISION_GATING,
    REPLAYERS,
    DecisionKind,
    DecisionRecord,
    Divergence,
    ForkResult,
    GatingMode,
    ReplayContext,
    fork_at_divergence,
    fork_for_diag_sibling,
    fork_for_sweep_sibling,
    record_decision,
    replay_decisions,
    resume_with_divergence_check,
)
```

Migrated callsites (one import shift each):

- `application/runner.py` — `Cycle` only from `cycle.py`.
- `application/optimization/cycle.py` — `DecisionRecord` from
  `run_records.py` (for `pending_decisions: list[DecisionRecord]`).
- `application/optimization/l1.py` — moved
  `DecisionKind`/`DecisionRecord`/`record_decision` to the new
  module; `Cycle` still from `cycle.py`.
- `application/optimization/escalation/firing.py` — same.
- `application/bootstrap/scoring_context.py` — `resume_with_divergence_check`.
- `application/sweep/sweep_runner.py` — `fork_for_sweep_sibling`.
- `presentation/cli/campaign_runner.py` — `fork_for_diag_sibling`,
  `DECISION_GATING`, `GatingMode`.
- `tests/test_rescore_and_fork.py` — every fork helper +
  `replay_decisions` (the test-name `_fork_at_divergence` lost its
  underscore since the function did).
- `tests/test_decision_kinds_registry.py` — `REPLAYERS`,
  `DECISION_GATING`, etc. all from one import.

## Out of scope for §3.6

- **Renames** — `Decision*` → `ResumeCheckpoint*`, `_fork_at_divergence`
  was renamed to `fork_at_divergence` (lost its leading underscore
  since it's now a public function within the resume_and_fork
  module). The broader §4.5 rename pass lands in commit 2.
- **Semantics** — what counts as divergence, when a fork mints, what
  `--from N` skips. §3.6 was pure file-layout consolidation.
- **Backward compat** — none. Imports break loudly; consumers
  migrate in lockstep with this commit.

## Verification

- `tests/test_rescore_and_fork.py` (15 tests) green throughout.
- `tests/test_decision_kinds_registry.py` (10 tests) green throughout.
- `python -m pytest -q` — 201 pass.
- `python -m ruff check . && python -m ruff format --check .` —
  clean.
- `python -m mypy promptpotter/` — no issues.
- `python -m deptry .` — clean.

The fork-on-divergence mechanism is the safety net for resume + scorer
swaps. After §3.6, debugging a fork failure is "open one module"
instead of "open five files and reverse-engineer the dance".
