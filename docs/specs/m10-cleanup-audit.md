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

## Next slices (per `m10-cleanup.md` §1, future PRs)

- Doc walk: README + per-directory CLAUDE.md tree + docs/concepts/ +
  docs/operations/ + docs/developer/ + docs/specs/ — tag each claim
  true/aspirational/dead.
- Code-violation grep list (8 grep targets — per-error retry,
  sidecar prompt-fill paths, hexagonal layer leaks, observed_node
  coverage, MeasurementArchive direct access, etc).
- Webapp + API + writers audit (`webapp/components/dashboard/`,
  FastAPI routes, `presentation/writers.py`).
- OSP field audit (22 fields → ≤18 own; map each to a §0 bucket).
- Free-deliverable verification (hard-sample dashboard data path).
- Self-optimization fixture (build `datasets/promptpotter/` per
  m10-cleanup §1 deliverable).
