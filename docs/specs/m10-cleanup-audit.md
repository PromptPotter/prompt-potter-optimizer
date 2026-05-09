# M10 Cleanup — Audit findings

**Audit scope:** `docs/architecture.md` §0 vocabulary verification
(per `m10-cleanup.md` Execution order step 1, narrow form).

**Method:** extract every backtick-quoted symbol/path/flag from §0
prose; for each, check whether it is (a) present today, (b) covered
by §0's vocabulary cross-walk table, or (c) drift to fix. Vocab-table
entries (target-state names + their today-state counterparts) are
audit-skipped — the table is the documentation.

**Result:** 61 total symbols. 56 verified clean; **4 issues found
and fixed in the same PR**.

## Findings

### Verified — present today (no action)

`CycleLedger.append`, `DegradationCheck`, `JobSearchPoint`,
`JobSearchPoint.content_hash` (`domain/search_point.py:73`),
`OptSearchPoint`, `dashboard.json`, `degradation_threshold`,
`events.jsonl`, `index.json`, `init` (CLI command), `l1_critique`,
`l1_generate`, `l1_score`, `l2_axis_yield_drought`, `l2_context`,
`l3_plan`, `log.md`, `max_rounds`, `measurements_for_sample()`,
`observed_node()`, `optimizer_pipeline.json`, `pipeline.json`,
`score_search_point()`, `task_context`, `validate_template()`,
`notebooks/optimization_campaign.ipynb`,
`notebooks/bbeh_potter.ipynb`, `--from N`, `--fork-on-divergence`
(both verified in `presentation/cli/campaign_runner.py`),
`promptpotter/application/optimization/elimination.py:222`,
`promptpotter/application/scoring/search_point_scorer.py:397`,
`promptpotter/infrastructure/tracing/file_sink.py:67`.

### Verified — vocab-table-only (audit-skip)

`SIGNALS`, `_Signal`, `SignalKind`, `INJECTIONS`, `_Injection`,
`InjectionKind`, `dispatch_hub.INJECTIONS`, `evaluate_round`,
`SignalInputs`, `firing.py`, `transitions.py`, `Bundle`,
`InjectionBundle`, `CycleLedger`, `CycleEventLog`, `ProjectionBase`,
`DerivedView`. Each is a target-state ↔ today-state pair documented
explicitly in §0's vocabulary cross-walk table.

### Verified — runtime-generated paths (not source files; doc accurate)

`archive/measurements/{run_id}.json`, `archive/measurements.json`,
`langfuse/events.jsonl` — these live under `.promptpotter/` after a
campaign runs; not in repo. §0 prose describes the on-disk format,
which is correct.

### Issues fixed in this PR

| # | Symbol in §0 | Reality | Fix |
|---|---|---|---|
| 1 | `tests/test_artifact_parity.py` | File does not exist. Single-writer / allowlist enforcement actually lives in `tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores` (line 491) + `test_artifact_sets_are_disjoint_and_well_formed` (line 112). | §0 reference updated to `tests/test_invariants.py` (named tests). m10-cleanup.md DoD entries updated to match. |
| 2 | `tests/test_layer_imports.py` | File does not exist. Hexagonal layer enforcement actually lives in `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations` (line 768) + `test_cycle_does_not_import_prompt_surface` (line 746). | §0 reference updated to `tests/test_invariants.py` (named tests). m10-cleanup.md §1 grep target text updated to match. |
| 3 | `webapp-react/` | Directory does not exist. Per the layered-disclosure pass + earlier consolidation (commit `f4c71498`), the canonical webapp dir is `webapp/`. | §0 reference updated `webapp-react/` → `webapp/`. |
| 4 | `docs/developer/pipeline-json-contract.md` | File does not exist yet — it's a `m10-cleanup.md` §3.5 deliverable. §0 currently asserts it as pinned. | §0 wording softened from "pinned in" to "to be pinned in (per m10-cleanup §3.5)". |

## Verification

After fixes land, re-running the audit script (inline in this PR's
commit message) should produce zero unverified symbols against
`docs/architecture.md` §0.

**Out-of-scope for this audit (deferred to doc-walk slice).** Root
`CLAUDE.md` still references the old test file names
(`tests/test_artifact_parity.py`, `tests/test_layer_imports.py`) on
lines 53, 82, 88, and `webapp-react` indirectly via a `docs/specs/`
filename. Those land in the broader §1 doc-walk PR, alongside the
README + per-directory CLAUDE.md tree audit.

## Doc-walk slice (broader §1 — completed)

**Scope:** root `CLAUDE.md`, per-directory CLAUDE.md tree
(`promptpotter/CLAUDE.md`, `application/CLAUDE.md`,
`domain/CLAUDE.md`, `infrastructure/CLAUDE.md`,
`presentation/CLAUDE.md`, `tests/CLAUDE.md`),
`docs/specs/CLAUDE.md`, `README.md`, `docs/concepts/`,
`docs/operations/`, `docs/developer/`. Tagged each cited symbol
against §0.

### Drift fixed in this slice (test-file rename cascade)

The narrow audit identified `tests/test_artifact_parity.py` and
`tests/test_layer_imports.py` as deleted files — their enforcement
lives in `tests/test_invariants.py` under named functions (verified
via `pytest --collect-only`). Cascaded that fix through every active
doc that named the old files:

| File | Line(s) | Old reference | New reference |
|---|---|---|---|
| `CLAUDE.md` | 53 | `tests/test_layer_imports.py` | `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations` + `test_cycle_does_not_import_prompt_surface` |
| `CLAUDE.md` | 82 | `tests/test_layer_imports.py` | `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations` |
| `CLAUDE.md` | 88 | `tests/test_artifact_parity.py` | `tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores` + `test_artifact_sets_are_disjoint_and_well_formed` |
| `promptpotter/application/CLAUDE.md` | 8 | `tests/test_layer_imports.py` | `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations` |
| `promptpotter/infrastructure/CLAUDE.md` | 28-29 | `tests/test_artifact_parity.py` | `tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores` + `test_artifact_sets_are_disjoint_and_well_formed` |
| `promptpotter/application/optimization/escalation/firing.py` | 4 | `tests/test_layer_imports.py` | `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations` |
| `docs/specs/CLAUDE.md` | 14 | `webapp/index.html` (deleted; vanilla webapp consolidated into Next.js export under `webapp/`) | `webapp/app/` |
| `docs/specs/CLAUDE.md` | 15 | `tests/test_artifact_parity.py` | `tests/test_invariants.py` |
| `docs/specs/m10-prompt-iteration-framework.md` | 75, 282 | `tests/test_artifact_parity.py::PER_CYCLE_AUDIT_ARTIFACTS` (deleted name) | `tests/test_invariants.py::PER_CYCLE_OPERATOR_ARTIFACTS` (current set name) |
| `docs/operations/persistence-and-state.md` | 126 | `tests/test_artifact_parity.py` | `tests/test_invariants.py` |

**Verification.** All cited test functions exist
(`pytest tests/test_invariants.py --collect-only -v` enumerates
`test_no_unexpected_runtime_layer_violations`,
`test_cycle_does_not_import_prompt_surface`,
`test_no_direct_artifact_writes_outside_stores`,
`test_artifact_sets_are_disjoint_and_well_formed`). Full suite
(`pytest -q`) still passes — markdown + one docstring touched, no
behavior change.

### Rename-cascade map (NOT fixed in this slice — pending §2/§3/§4/§4.5 PRs)

Per `m10-cleanup.md` §1 ("the audit produces the rename-cascade map
… so the rename PRs update them in lockstep"), recording which
file mentions which old symbol. These are correct against today's
code; they get updated when the rename PR lands.

| Old symbol (today) | Target (per §) | Files that cite it |
|---|---|---|
| `cadence` / `cadence/` / `cadence.evaluate_round` / `CadenceRule` / `DEFAULT_ROUND_RULES` / `SignalInputs` | §3 → folded into `escalation/`; `decide_escalation` / `EscalationRule` / `DEFAULT_ESCALATION_RULES` / `EscalationInputs` | `promptpotter/CLAUDE.md`, `promptpotter/application/CLAUDE.md`, `tests/CLAUDE.md`, `docs/operations/persistence-and-state.md`, `docs/operations/observability.md`, `docs/developer/dispatch-hub.md`, `docs/developer/README.md`, `docs/concepts/the-loop.md`, `docs/concepts/glossary.md`, `docs/concepts/README.md`, `docs/concepts/campaign-tree.md`, `docs/developer/l2-internals.md`, `docs/developer/l1-generate-surface.md` |
| `SIGNALS` / `_Signal` / `SignalKind` | §2 → `INJECTIONS` / `_Injection` / `InjectionKind` | `promptpotter/CLAUDE.md`, `docs/developer/dispatch-hub.md`, `docs/developer/README.md` |
| `DecisionKind` / `DECISION_GATING` / `DecisionRecord` | §4.5 → `ResumeCheckpointKind` / `RESUME_CHECKPOINT_GATING` / `ResumeCheckpointRecord` | `promptpotter/domain/CLAUDE.md`, `CLAUDE.md` (line 88) |
| `CycleLedger` / `CycleLedger.append` / `CycleLedger.inherit_from` | §4.5 → `CycleEventLog` (or `CycleJournal`) | `promptpotter/infrastructure/CLAUDE.md` (lines 9–12), `promptpotter/presentation/CLAUDE.md` (line 21), `CLAUDE.md` (line 88), most `docs/operations/` + `docs/developer/` |
| `ProjectionBase` | §4.5 → `DerivedView` (or `LedgerSubscriber`) | `promptpotter/infrastructure/CLAUDE.md` (line 25), `CLAUDE.md` (line 88) |
| `compile_l1_surface` / `compile_l2_surface` | §4.5 → `compile_l1_field_catalogue` / `compile_l2_field_catalogue` | `docs/specs/CLAUDE.md` (M10 row pre-reading) |

### Drop-cascade map (NOT fixed in this slice — pending §4 PRs)

Surfaces marked for removal in §4. Docs correctly describe today's
code; cleanup ships in lockstep with the §4 drop PR.

| Symbol / surface (today) | §4 disposition | Files that cite it |
|---|---|---|
| `DecisionTrace` / `domain/decision_trace.py` / `RoundResult.decision_traces` / `decision_trace_summary` injection | drop | `CLAUDE.md` (line 60), `docs/developer/dispatch-hub.md` (multiple), `docs/developer/README.md` |
| `SignalsProjection` / `.runtime/signals.jsonl` / `recent_rules` / `current_signals` | drop | `tests/CLAUDE.md`, `docs/operations/observability.md`, `docs/operations/persistence-and-state.md` (line 106) |
| `SignalsPanel.tsx` / `StuckDiagnosis.tsx` | drop | `docs/operations/observability.md` (line 50), `docs/operations/persistence-and-state.md` (line 106) |
| `scoring_steer.json` mid-campaign hot-swap | drop | `CLAUDE.md` (line 92), `docs/operations/persistence-and-state.md` (line 232) |
| zero-signal sample filter | drop | `CLAUDE.md` (line 96), `docs/concepts/scoring-and-memory.md` (lines 111, 114) |
| scoring-set evolution | drop | `CLAUDE.md` (line 96), `README.md` (line 112), `docs/concepts/scoring-and-memory.md` (lines 111, 114) |

### Out-of-scope (verified untouched)

- `docs/specs/archive/` — historical, leave.
- `docs/specs/m9-stable-config-and-scaffolding.md` — M9 complete, retained for historical context per `docs/specs/CLAUDE.md` row.
- `docs/specs/m11-webapp-react-port.md` — historical migration spec; the `webapp-react/` references describe the actual migration path that was taken (status: shipped 2026-05-07) and are accurate as historical record.
- Notebook lint drift in `docs/research/bbeh-comparison/*.ipynb` and `notebooks/optimization_campaign.ipynb` — pre-existing, verified via `git stash` round-trip; orthogonal to this audit slice; needs its own cleanup PR.

## Next slices (per `m10-cleanup.md` §1, future PRs)

- Code-violation grep list (8 grep targets — per-error retry,
  sidecar prompt-fill paths, hexagonal layer leaks, observed_node
  coverage, MeasurementArchive direct access, etc).
- Webapp + API + writers audit (`webapp/components/dashboard/`,
  FastAPI routes, `presentation/writers.py`).
- OSP field audit (22 fields → ≤18 own; map each to a §0 bucket).
- Free-deliverable verification (hard-sample dashboard data path).
- Self-optimization fixture (build `datasets/promptpotter/` per
  m10-cleanup §1 deliverable).
- Notebook lint cleanup (pre-existing ruff drift in
  `docs/research/bbeh-comparison/*.ipynb` +
  `notebooks/optimization_campaign.ipynb`) — own commit.
