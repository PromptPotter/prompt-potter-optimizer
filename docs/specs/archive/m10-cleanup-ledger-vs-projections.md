# M10 Cleanup — Ledger vs. projections duplication map

**Audit scope:** every projection-written field/file mapped to its
ledger source (or flagged PROJECTION-ONLY / DRIFT). Per
`m10-cleanup.md` §1: "the same fact written by both
`CycleLedger.append` and a projection's own file write" is drift —
pick one source per fact.

## Ledger event types (today)

Four `CycleRecord` subtypes land in `events.jsonl`:

| Type | Emitted for | Key fields |
|---|---|---|
| `DecisionRecord` | Resume checkpoints (round_winner, elimination_cut, leader_lock_in, l2/l3 escalation triggers, probe_round_commitment, fork_cut) | `kind`, `inputs_ref`, `outcome`, `data`, `round`, `timestamp` |
| `PhaseRecord` | Campaign phase boundaries: `round`, `origin`, `init`, `l1_generate`, `l1_score`, `refine_strategy`, `modify_plan`, `cadence`, `escalation`, `scoring_steer` | `phase`, `event`, `round`, `payload`, `timestamp` |
| `SnapshotRecord` | In-flight live state: `sample_started`, `sample_scored`, `candidate_started`, `candidate_scored`, `p_best_update` | `event`, `round`, `candidate_idx`, `sample_idx`, `payload`, `timestamp` |
| `TokenUsageRecord` | LLM call telemetry | `kind`, `node`, `model`, `input_tokens`, `output_tokens`, `duration_s`, `cost_usd`, `round`, `timestamp` |

## Per-projection write map

### LiveDashboardProjection — `dashboard.json` + `output.log`

| Field | Source | Status |
|---|---|---|
| `state` (phase name) | `PhaseRecord` via `_handle_phase` | CLEAN |
| `round` | `SnapshotRecord` + `PhaseRecord` | CLEAN |
| `origin` | `PhaseRecord(INIT:exit)` payload | CLEAN |
| `best` | derived from `SnapshotRecord(sample_scored)` | CLEAN |
| `current_round.nodes.l1_generate` / `.l1_critique` / `.l2_context` / `.l3_plan` | populated by `AuditTrailProjection.snapshot_nodes()` direct call | PROJECTION-ONLY |
| `current_round.nodes.l1_score` | built from `SnapshotRecord(sample_scored)` payloads | CLEAN |
| `current_round.candidates[]` (samples / scores / p_best*) | `SnapshotRecord` payloads | CLEAN |
| `spend.backend` / `spend.loop` / `spend.total_used_usd` | `TokenUsageRecord` via `_handle_token_usage()` | CLEAN |
| `current_acc`, `total_queries_scored`, `total_backend_calls` | `SnapshotRecord` aggregates | CLEAN |
| `composite_fitness_formula` | `PhaseRecord(INIT:exit)` or `PhaseRecord(scoring_steer:applied)` | CLEAN |
| `cycle_id` | `PhaseRecord(INIT:exit)` + direct `log_fork()` call | PROJECTION-ONLY (partial) |
| `recent_rules` (rolling cap=8) | `PhaseRecord(cadence/rule_fired)` via `_absorb_rule_fired()` | **DRIFT** (also written by `SignalsProjection`) |
| `current_signals[layer]` | `PhaseRecord(cadence/rule_fired)` via `_absorb_rule_fired()` | **DRIFT** (also written by `SignalsProjection`) |
| `output.log` | not written by projection (CLI stdout only) | N/A |

### AuditTrailProjection — `.runtime/cache/rounds/round_NNNN.json`

| Field | Source | Status |
|---|---|---|
| `round` | `PhaseRecord(round/enter)` | CLEAN |
| `started_at` / `finished_at` | direct timestamps on `begin_round()` / `flush()` | PROJECTION-ONLY |
| `nodes.l1_generate` | `LLMCallRecord` ledger event via `_handle_llm_call` | CLEAN (resolved per Note 1) |
| `nodes.l1_critique` | `LLMCallRecord` ledger event via `_handle_llm_call` | CLEAN (resolved per Note 1) |
| `nodes.l2_context` | `LLMCallRecord` ledger event via `_handle_llm_call` | CLEAN (resolved per Note 1) |
| `nodes.l3_plan` | `LLMCallRecord` ledger event via `_handle_llm_call` | CLEAN (resolved per Note 1) |
| `nodes.l1_score` | deposited by `LiveDashboardProjection.set_l1_score()` | CLEAN (via LiveDashboard) |

**Note 1 — RESOLVED via M10 commit 1.** Operator picked the
single-writer path: every optimizer LLM call now emits
`LLMCallRecord` (a typed `CycleRecord` subtype) into the ledger.
`AuditTrailProjection` consumes the record via
`_handle_llm_call(LLMCallRecord)` and projects it into the round's
`nodes.<node>` block — the audit trail file becomes a pure derived
view. The `add_action` direct write entry point is gone;
`run_optimizer_node` takes a `ledger: CycleLedger | None`
parameter and emits one record per call (real LLM call, cached
hit, or synthesized "loaded from disk" event with
`payload_kind="synthesized"`).

### PoBBStreamProjection — `.runtime/streams/round_NNNN_p_best.jsonl`

| Field | Source | Status |
|---|---|---|
| `round`, `sample_idx`, `current_id`, `n_samples`, `p_best`, `p_best_delta` | `SnapshotRecord(p_best_update)` via `_handle_snapshot()` | CLEAN |

Pure derivation. No drift.

### SignalsProjection — `.runtime/signals.jsonl`

| Field | Source | Status |
|---|---|---|
| `round`, `timestamp`, `layer`, `rule_name`, `rule_priority`, `next_action`, `reason`, `signal_inputs` | `PhaseRecord(cadence/rule_fired)` via `_handle_phase()` | **DRIFT** |

Per §4 drop list — `SignalsProjection` is being removed. Today it
writes the same cadence rule-firing facts that
`LiveDashboardProjection` writes to `dashboard.json::recent_rules`
+ `current_signals`. Both source from the same ledger event.

### LiveStateCore — in-memory shared scalars

Not a file writer; transient cache shared between projections. No
drift analysis applicable.

## Drift resolutions

| Duplicated fact | Ledger source | Today's writers | Resolution |
|---|---|---|---|
| Cadence rule firing (`round`, `layer`, `rule_name`, `rule_priority`, `next_action`, `reason`, `signal_inputs`) | `PhaseRecord(phase="cadence", event="rule_fired")` | (a) `LiveDashboardProjection` → `dashboard.json::recent_rules` (cap=8) + `dashboard.json::current_signals[layer]`. (b) `SignalsProjection` → `.runtime/signals.jsonl` (full trail). | Per §4: drop `SignalsProjection`. **Sole writer: `LiveDashboardProjection`.** Full trail reconstructable from `events.jsonl` on demand (derivation, not ground truth). |

## Counts

- Projection-written fields inventoried: ~40
- CLEAN (derived from ledger): ~35
- PROJECTION-ONLY (no ledger source): ~6 (LLM node I/O in audit trail, timestamps, partial cycle_id)
- DRIFT (both write same fact): 1 logical fact (cadence rule firing) at 3 write locations
  → resolved cleanly by §4 drop of `SignalsProjection`

## Out of scope (flagged)

The PROJECTION-ONLY status of the audit trail's LLM node I/O
(`nodes.l1_generate` / `.l1_critique` / `.l2_context` / `.l3_plan`)
is a real architectural question — not drift in the
"two-writers-of-the-same-fact" sense, but a question of whether the
audit trail counts as a second persistence channel alongside the
ledger. Disposition belongs in a §0 update or a §3.x sub-spec, not
this audit.
