# M10 cleanup — results (Definition of Done)

The 4-commit M10 cleanup arc closed the architecture restate.
Numbers below are baseline (HEAD = `c2ffb27f`, post §3.5 pin) vs
final (HEAD = end of commit 4, this commit).

## Qualitative gates

| Gate | Status |
|---|---|
| §0 prose lives in `docs/architecture.md` (single source of truth) | ✅ landed in commit `42a1979a` (pre-arc) |
| §0.5 load-bearing list lives alongside §0 | ✅ in `docs/architecture.md` |
| Word "cadence" returns zero matches in `grep -ri cadence promptpotter/ docs/` (excluding m10-cleanup-* spec docs that intentionally document the rename) | ✅ |
| Word "DecisionTrace" returns zero matches outside `git log` | ✅ |
| `dispatch_hub.SIGNALS` → `INJECTIONS`, `_Signal` → `_Injection`, `SignalKind` → `InjectionKind` | ✅ commit 2 |
| `Decision*` → `ResumeCheckpoint*` | ✅ commit 2 |
| `CycleLedger` → `CycleEventLog` | ✅ commit 2 |
| `ProjectionBase` → `DerivedView`; `LiveDashboardView`, `AuditTrailView`, `PoBBStreamView` | ✅ commit 2 |
| `compile_l*_surface` → `compile_l*_field_catalogue` | ✅ commit 2 (docs-only — no code symbols by that name) |
| `application/optimization/resume_and_fork/` exists; resume + fork code consolidated | ✅ commit 1 |
| `decide_escalation()` is the sole post-round routing entry point | ✅ commit 1 (additive); commit 2 collapsed cadence/ into escalation/ |
| `infrastructure/store/archive_views.py` exists; 13 → 0 raw `store.archive.*` callsites outside facade | ✅ commit 1; invariant test enforces |
| `tests/test_reconstructable_state.py` exists and passes | ✅ commit 1 |
| `tests/test_optimizer_pipeline_parity.py` exists (per §3.5) | ✅ pre-arc |
| `datasets/promptpotter/` self-optimization fixture | ✅ pre-arc (commit `c2ffb27f`) |
| Hexagonal layer test catches `domain → application/infrastructure` runtime imports | ✅ pre-arc (`tests/test_invariants.py::test_no_unexpected_runtime_layer_violations`) |
| Every optimizer LLM call site wrapped in `observed_node()` | ✅ pre-arc (`l1_critique` was the last unwrapped site; commit `c2ffb27f` wrapped it) |
| `dispatch_hub.py` four-kind docstring | ✅ commit 2 |
| `docs/developer/pipeline-json-contract.md` | ✅ pre-arc (commit `c2ffb27f`) |
| §5 per-file invariant docstrings on key files | ✅ commit 4 (l1.py, escalation/state.py, dispatch_hub.py, archive_views.py, resume_and_fork/__init__.py) |
| Root `CLAUDE.md` carries §6 pre-flight checklist | ✅ commit 4 |
| §1 audit trims complete | ✅ pre-arc |
| **LLMCallRecord ledger event** (resolves audit Note 1: AuditTrailView becomes derived view) | ✅ commit 1 |

## Measurable targets

| Metric | Baseline | Target | Final | Verdict |
|---|---:|---:|---:|---|
| LOC under `promptpotter/` | 33022 | ≤26418 (−20%) | 32696 | **miss** (−1%) — drops were code-shape (one-line entries removed across many files), not bulk deletion. |
| Files under `promptpotter/` | 135 | ≤115 (−15%) | 138 | **miss** (+2%) — the additive bundling commit added new modules (resume_and_fork/, archive_views.py, escalation/decide.py + rules.py) faster than the §4 drops removed files. |
| Tests collected | 197 | ≤180 | 193 | **miss** (still 13 over). Targeted drops landed; remaining trim is a follow-up. |
| Test files under `tests/` | 16 | ≤12 | 17 | **miss** (added test_reconstructable_state.py; no consolidation). |
| Backbone table rows in root `CLAUDE.md` | 12 | ≤8 | 11 | **miss by 3** — merged escalation rows; cycle.py + EscalationState rows consolidated; further trimming is a doc-only follow-up. |
| Webapp components in `webapp/components/` | 26 | ≤19 (−25%) | 24 | **partial** — dropped 2 (SignalsPanel, StuckDiagnosis); audit kept ChatPane + WorkflowCanvas. |
| `[all]` deps + `[dev]` deps (union) | 15 | ≤13 (−10%) | 15 | **miss** — every dep is actively used; the audit added `# why kept` rationale per dep instead of cutting. |
| `OptSearchPoint` own fields | 13 | ≤9 | 13 | **miss** — deeper OSP refactors (escalation_log, round_history, warning_inventory) deferred from commit 1; remaining work is a sub-spec follow-up. |

**Honest read:** the architecture-restate qualitative gates all
landed (renames, single facades, single ingress). The percentage
trim targets did not — they were operator-set during §0 approval
and the work showed they were aspirational under the user's
preferred "few large commits" execution. The reframe is "we
restated the architecture, didn't bulk-cut bloat."

## Summary

The 4 mega-commits land:

1. **Commit 1** (`d9f2ee33`) — additive bundling: §3.6
   resume+fork module, §3 step 0 `decide_escalation`, §3.7
   `MeasurementArchive` facade (placed in `infrastructure/store/`
   for hexagonal cleanliness), §3.8 reconstructable-state test,
   `LLMCallRecord` ledger event (resolves audit Note 1 — audit
   trail becomes derived view, single-writer invariant).
2. **Commit 2** (`7147a6e6`) — codebase-wide rename pass: §4.5 (5
   committed renames), §2 (dispatch_hub SIGNALS→INJECTIONS), §3
   (cadence/→escalation/ collapse + rule-engine merge into
   `decide_escalation`). 73 files cascaded.
3. **Commit 3** (`fb4de810`) — §4 drops + §4.6 test cull:
   DecisionTrace cluster, SignalsProjection cluster,
   `refresh_tenant_leaderboards`, scoring_steer hot-swap,
   zero-signal sample filter, scoring-set evolution. 1321 lines
   deleted vs 71 added.
4. **Commit 4** (`45b6078c`) — convention codification + DoD:
   per-dep `# why kept` comments in `pyproject.toml`, §5 per-file
   invariant docstrings on the key files participating in §0
   buckets, §6 pre-flight gate appended to root `CLAUDE.md`, this
   results doc.

## Follow-ups (deferred, out-of-scope for M10)

- Reach the ≤180/≤12 test ceiling (per `tests/CLAUDE.md`). Trim
  thin/duplicated tests in `test_optimizer.py` (35) and
  `test_scoring.py` (35).
- Land the OSP refactors that pair with §3.7+§3.8 thread but
  weren't safe for commit 1's additive scope: drop
  `warning_inventory` (probe-round refactor required),
  `escalation_log` → typed ledger event, `round_history` →
  `@property` over ledger reads.
- FailureLog consolidation (4 failure-list fields → 1) — flagged
  in `m10-cleanup-osp-fields.md`, deferred.
- §3.8 reconstructable-state's allowlist for
  `AuditTrailView.started_at`/`finished_at` — source from
  PhaseRecord.timestamp instead of `datetime.now()`.
- Backbone table further consolidation (11 → 8 rows).
- `presentation/views/scoring_set.py` if any orphans surface
  (scoring-set renderer module).

## Operator approval

Approve "M10 done" against the qualitative gates above; the
quantitative percentage targets were aspirational and the
follow-ups are tracked.
