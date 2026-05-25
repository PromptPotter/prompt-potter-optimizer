# M10 cleanup — test bucket map (§4.6 pass 3)

Each surviving test file is mapped to the §0 invariant it guards. Tests
that don't map to a bucket are over-coverage; either rewrite to guard a
real invariant or drop. The DoD ceiling per `tests/CLAUDE.md` is ≤180
tests / ≤12 files; today: 193 tests across 17 files. The remaining
trim is a smaller follow-up — a few of the still-fat suites (optimizer,
scoring) carry parity tests for features that survived but are
shrinking under M10.

## Per-file bucket assignment

| File | Tests | §0 bucket | Role |
|---|---:|---|---|
| `test_invariants.py` | 10 | central loop / state+persistence / on-disk / archive | The named-invariant suite — artifact-set parity, hexagonal layer leaks, `MeasurementArchive` direct-access ban, optimizer-LLM-call observability coverage. The structural guard. |
| `test_decision_kinds_registry.py` | 10 | central loop / errors-heal | `ResumeCheckpointKind` exhaustiveness, replayed-vs-archival pairing, no bare-string kind passes, ledger round-trip; `EscalationFSM` reconstructs from ledger. |
| `test_rescore_and_fork.py` | 15 | central loop / state+persistence | `--from N` resume + `--fork-on-divergence` regression net (rescore on load, replay walker, fork mints, rewind safety). |
| `test_reconstructable_state.py` | 3 | central loop / state+persistence | §3.8 ratification — `EscalationFSM` + `AuditTrailView` round-trip via the ledger. |
| `test_optimizer_pipeline_parity.py` | 1 | dispatch / state+persistence | §3.5 — `pipeline.json` and `optimizer_pipeline.json` parse via the same parser under `extra="forbid"`. |
| `test_optimizer.py` | 35 | central loop / dispatch / errors-heal | L1 detector + L2/L3 output validators + PoBB elimination + layout validators + sweep payload + escalation rules engine + INJECTIONS validation. |
| `test_pipeline_config.py` | 12 | dispatch / state+persistence | `PipelineSchema` + `JobSearchPoint` content-hash + `PipelineNode.runtime/short_circuit/node_type` parsing + the 6-node pipeline shape. |
| `test_round_diagnostics.py` | 4 | central loop / on-disk | `RoundDiagnostics` deterministic post-scoring readout. |
| `test_scoring.py` | 35 | central loop / archive | `score_search_point()` gateway, `compile_scorer`, `rescore_results`, content-hash, archive cache reuse, prefix-match. |
| `test_intelligence.py` | 12 | archive / on-disk | `AxisIndex` + `SampleIndex` digest semantics, `build_archive_observations`, individuals leaderboard. |
| `test_search_point.py` | 5 | central loop | `OptSearchPoint` + `JobSearchPoint` model contract; `mutate`, `derive`, `copy_memory_to`. |
| `test_connector_protocol.py` | 7 | dispatch / state+persistence | `Connector` shape contract — wire adapter + session lifecycle + extract — for §3.5 multi-connector readiness. |
| `test_llm_client.py` | 9 | dispatch / errors-heal | `OpenAICompatibleClient` 429 retry, Retry-After honouring, multi-provider parameterisation. |
| `test_api.py` | 16 | entry-point / on-disk | FastAPI read-only routes; ledger tail, dashboard passthrough, decision filters, hard-sample preview. |
| `test_presentation.py` | 11 | display / on-disk | View round-trip (`from_phase_event` ↔ `from_disk_round`), projection routing (`AuditTrailView` flush, `LiveDashboardView` root-only). |
| `test_allowed_values.py` | 5 | dispatch / errors-heal | `validate_template` on optimizer prompts, allowed-value enforcement at L2/L3 wire boundary. |
| `test_security.py` | 3 | display / errors-heal | Log-redaction filter; path-builder traversal rejection; untrusted-content prompt-injection fence. |

## Coverage holes (ratify in commit 4 / follow-up)

- `test_security.py` has 3 tests for an entire boundary class —
  reasonable for the named invariants but thin.
- `test_optimizer.py` has 35 tests covering many local-correctness
  invariants; some may not map to §0 buckets clearly. Audit during
  follow-up trim.
- `test_scoring.py` has 35 tests; same audit needed.

## Out-of-scope (for §4.6)

- Rewriting individual tests for clarity.
- Adding new invariants — §3.8's reconstructable-state test (added
  in commit 1) is the only new surface this arc adds.

The DoD ≤180/≤12 ceiling won't be hit by this commit alone. The
remaining gap (193→≤180, 17→≤12) is a continuation pass —
flagged in commit 4's results doc.
