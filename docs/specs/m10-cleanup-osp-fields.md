# M10 Cleanup — OptSearchPoint field audit

**Audit scope:** every field on `OptSearchPoint`
(`promptpotter/domain/opt_search_point.py`) mapped to a §0 bucket
+ writer + reader + `MEMORY_FIELDS` membership. Per
`m10-cleanup.md` §1: "Fields without a bucket, or without both a
writer and a reader, are drift. Drop the orphans in §4."

## Count correction

Spec (`m10-cleanup.md` §0 measurable targets) cites **22 (14 own +
8 inherited)**. Actual today: **21 (13 own + 8 inherited)** — spec
count is stale by one. Drop target should be re-baselined in the
results doc; "cut at least 4 own" → 13 - 4 = 9 own fields surviving.

## Inherited from `PromptTemplate` (8 fields — STAY per spec)

| Field | Type | §0 bucket | Verdict |
|---|---|---|---|
| `persona` | `str` | L2 surface | KEEP |
| `task_intent` | `str` | L2 surface | KEEP |
| `problem_description` | `str` | L2 surface | KEEP |
| `instruction` | `str` | L2 surface | KEEP |
| `thinking_style` | `str` | L2 surface | KEEP |
| `answer_format` | `str` | L2 surface | KEEP |
| `few_shot_examples` | `list[FewShotExample]` | L2 surface | KEEP |
| `plan` | `str` | L3 surface | KEEP |

Writer for all eight: `l2_context` LLM (the seven prompt fields)
and `l3_plan` LLM (`plan`). Reader: `render()` → every prompt via
the dispatch hub. All clean.

## Own fields (13 — audit candidates)

| # | Field | Type | §0 bucket | Writer | Reader | In `MEMORY_FIELDS`? | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | `lineage` | `IndividualLineage` | lineage | `mutate()` at creation | `copy_memory_to()`, fork machinery, log rendering | no | KEEP |
| 2 | `l1_config` | `dict[str, Any]` | L1 surface (L2-authored) | `l2_context` LLM via `firing.py::_apply_l2`; `apply_sweep_payload_to_osp` | `dispatch_hub._r_l1_config`, `l1.py::execute_round` | **no** | **RENAME → `l1_overrides` + ADD to MEMORY_FIELDS** (clarity + L3→L2 inheritance fix) |
| 3 | `task_context` | `TaskDecomposition` | L2 surface | `l2_context` LLM (merge into dict) | `dispatch_hub._r_task_context_formatted`, every prompt | no | KEEP |
| 4 | `escalation_log` | `list[dict]` | memory | `append_escalation()` in `runner.py:498` | `dispatch_hub._r_escalation_panel` | yes | **REFACTOR-PENDING** — better modeled as a `CycleLedger` event (`escalation_fired` with typed fields). Sidecar drift today, ledger-resident in §3.x. |
| 5 | `warning_inventory` | `dict[str, dict]` | memory | `firing.py` L2/L3 apply; `cycle.py` reconciliation | `dispatch_hub._r_escalation_panel` (formats from the failure lists, NOT from this dict) | yes | **DROP** — duplicate channel. Failures already render via `validation_failures` + `runtime_failures`; no reader uses `warning_inventory` as primary source. |
| 6 | `l3_note` | `str` | memory | `l3_plan` LLM | `dispatch_hub._r_l3_to_l2_note` | yes | KEEP |
| 7 | `validation_failures` | `list[ValidationFailure]` | memory | `l1_validators.py`, `l1_behavior_checks.py` | `dispatch_hub._r_validation_failures`, `l1_critique` | yes | KEEP (but see consolidation candidate below) |
| 8 | `runtime_failures` | `list[RuntimeFailure]` | memory | `cycle.py:619` during evaluation | `dispatch_hub._r_runtime_failures`, `metrics.py` | yes | KEEP (but see consolidation candidate below) |
| 9 | `l2_guard_breaches` | `list[ValidatorOutcome]` | memory | `l2_validators.py` | `dispatch_hub._r_l2_guard_breaches` | yes | KEEP (consolidation candidate) |
| 10 | `l3_guard_breaches` | `list[ValidatorOutcome]` | memory | L3 plan output validation | `dispatch_hub._r_l3_guard_breaches` | yes | KEEP (consolidation candidate) |
| 11 | `failure_analysis` | `FailureAnalysis \| None` | scoring projection | `metrics.py::compile_failure_analysis` | `dispatch_hub._r_failure_summary` | yes | KEEP |
| 12 | `round_history` | `list[RoundSummary]` | scoring projection | `cycle.py` per-round append | leaderboard view, log markdown | yes | **CONSIDER REPLACING with a property** — derivable from ledger `SnapshotRecord` aggregates + `DecisionRecord(round_winner)`. Today stored explicitly; could be a `@property` over ledger state once §3.8 reconstructable-state invariant lands. |
| 13 | `l1_layout` | `L1Layout` | L1 surface (L2-authored) | `l2_context` LLM via `firing.py::_apply_l2` + `apply_sweep_payload_to_osp` | dispatch hub walks at L1 fill time | yes | KEEP |

## Cuts summary

**Clean drops (immediate, §4-eligible):**

- `warning_inventory` — duplicate of `validation_failures` /
  `runtime_failures`; no reader treats it as source of truth.

**Refactors / renames (smaller PRs):**

- `l1_config` → `l1_overrides` rename + add to `MEMORY_FIELDS` (so
  L3-spawned children inherit L1 knob tweaks instead of silently
  reverting).
- `escalation_log` → migrate to a typed `CycleLedger` event
  (`escalation_fired`); pairs with §3.7 facade work and §3.8
  reconstructable-state invariant.
- `round_history` → convert to `@property` over ledger
  (`SnapshotRecord` round-end aggregates + `DecisionRecord`
  winners). Pairs with §3.8.

**Consolidation candidates (design decision, not immediate):**

The four failure-list fields (`validation_failures`,
`runtime_failures`, `l2_guard_breaches`, `l3_guard_breaches`)
could be unified into a single `FailureLog` dict keyed by source
layer, with `failure_analysis` as the derived summary view. That's
a design decision (one field vs. four) — not an audit verdict.
Flag for §4.5 rename pass or a separate sub-spec.

## Counts vs spec target

Spec target: ≤18 total (13 own - 4 = 9 own + 8 inherited = 17, or
the spec's "≤18" allows 10 own).

Immediate drops from this audit: 1 (`warning_inventory`).

To hit the cut target without touching §3.7/§3.8 work, additional
drops require either:
- Consolidating the four failure-list fields into one
  `FailureLog` (saves 3).
- Converting `round_history` to a property in this PR (saves 1).
- Treating `l1_config` rename as also a "cut" (debatable — it's a
  rename, not a drop).

Each consolidation has implementation cost; the audit's role is to
surface candidates. The actual cut PR (§4) decides the bundle.

## Out of scope (not a field audit concern)

- The 8 inherited prompt fields are the
  field-standard-PromptWizard-shape and stay per §0.
- `MEMORY_FIELDS` membership for current fields is correct (already
  audited above; only `l1_config` should join).
- Method-level audit (`copy_memory_to`, `append_escalation`,
  `_field_value`, etc.) — separate concern.
