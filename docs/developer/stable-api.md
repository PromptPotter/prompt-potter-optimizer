# Stable API surface — what forks can rely on

> **Stable API v1** · Last reviewed: 2026-05-14

This document defines what downstream forks of PromptPotter can build
on without breaking on the next refactor. Anything not listed here is
**internal** — free to rename, restructure, or delete in any PR. Forks
that depend on internal symbols are on their own.

Versioning rule: this doc carries a single integer version
(`Stable API v1`). Breaking changes to anything below bump the major
and get called out in the release notes. Pre-fork (today) the version
is informational only — there are no released versions. Once the
project ships its first public release, version bumps become contract.

Out of scope from this doc:

- Internal module structure (the per-package layout, file names,
  import paths inside `promptpotter/*` beyond what's listed below).
- Private types (anything `_`-prefixed, dataclasses not re-exported
  through their package `__init__`, helpers under `application/` /
  `infrastructure/` / `presentation/`).
- Runtime dataclass shapes that are not explicitly named here.
- In-memory caches (the optimizer LRU caches, the dispatch hub's
  pipeline-param-catalogue cache, etc.).
- **The prompt templates themselves** — they live under
  `datasets/_optimizer/pipeline.json::resolved_prompts` and are
  intentionally tunable. Forks may edit them; this doc does not
  freeze their wording.

---

## 1. Connector protocol

Frozen dataclass at `promptpotter/connectors/protocol.py::Connector`:

```python
@dataclass(frozen=True)
class Connector:
    name: str                                                       # lowercase id; matches pipeline.json::backend_type
    wire_adapter: Callable[[str, dict | None], dict]                # outbound HTTP body shaper
    session_factory: Callable[[], SessionProtocol]                  # fresh session per BackendClient
    extract_experiment: Callable[[dict], tuple[list[dict], list[str]]]  # backend experiment data → (queries, index_terms)
    resolve_ground_truth: Callable[[dict, str], str | None]
```

`SessionProtocol` lives at `promptpotter/domain/connector.py`:

- `async set_terms(terms: list[str]) -> None` — backend handshake; noop is acceptable.
- `async recover() -> None` — re-establish session after a transport error.

Registration: each connector self-registers at import via the dict in
`promptpotter/connectors/__init__.py::CONNECTORS`. Adding a connector
is one new file in `promptpotter/connectors/`; no edits to
`application/config.py` or `infrastructure/backend.py`.

Reference implementations:
[`connectors/termnorm.py`](../../promptpotter/connectors/termnorm.py),
[`connectors/promptpotter.py`](../../promptpotter/connectors/promptpotter.py).

Wire-adapter contracts beyond `protocol.py`:

- Wire adapters MUST be pure functions: `(query, pipeline_params) -> dict`.
  No I/O, no logging beyond debug-level drops.
- `extract_experiment` MUST return `(queries, index_terms)`. The
  `index_terms` list may be empty for connectors with no retrieval index.
- `resolve_ground_truth` MUST return `str | None`.

---

## 2. Scoring formula DSL

Per-dataset scoring is configured via `campaign.json::scoring`:

```jsonc
{
  "scoring": {
    "per_sample": "acc",                        // required: scorer expression
    "per_round": "median(scores)",              // optional: round-level aggregator
    "scorer_id": "acc_v1"                       // optional: explicit id
  }
}
```

**Addressable namespace** in the scorer expression
(`application/scoring/formula/compiler.py`):

- **Builtins**: `min`, `max`, `sum`, `mean` (where `mean` is the
  arithmetic mean — `statistics.fmean`). Anything else from `__builtins__`
  is **not** addressable.
- **Per-sample evaluators**: any function registered in the evaluator
  registry. Today: `acc` (rank-1 exact match), `gt_in_ranked_items`,
  `gt_in_source`, `composite` (multi-axis blend). Evaluator names are
  stable; the underlying implementation may change.
- **Per-round aggregators**: `mean`, `median`, `min`, `max`, `count`,
  plus the same evaluator names lifted to round level.

Constants, name lookups, and arithmetic operators (`+`, `-`, `*`, `/`,
`%`, `**`) are addressable. **Function calls outside the registry are
rejected at compile time** (this is enforced; not a convention).

Two stable signals every measurement carries:

- **`hit`** — boolean, rank-1 exact match. Feeds per-sample
  classification (SampleIndex / cohort analysis).
- **`score`** — continuous, formula-driven. Feeds the optimizer.

---

## 3. Dataset config schema

Each dataset lives at `datasets/{name}/` with these files:

### `pipeline.json`

Connector-described pipeline. Same shape the backend exposes via
`GET /pipeline`, plus an operator overlay.

Required top-level keys:

- `name` (string) — pipeline name.
- `version` (string) — pipeline version.
- `backend_type` (string) — connector name. Must match a registered connector.
- `backend_name` (string) — display name shown in operator surfaces.
- `nodes` (object) — node graph. Each node has:
  - `runtime` (string) — `python` / `llm` / `cache` / `network`.
  - `short_circuit` (bool) — does this node terminate the pipeline?
  - `node_type` (string) — `candidate_source` / `ranker` / `enricher` / `cache` / `""`.
  - `optimizer.param_keys` (list[string]) — operator-tunable knobs.
  - `optimizer.observation_mappings` (object) — wire-name → optimizer-name map.
  - `optimizer.langfuse_type` (string) — observability tag.
  - `config` (object) — per-dataset overlay; merged onto the wire payload.
- `pipelines` (object) — named pipeline variants.
- `available_models` (list[string]) — model menu shown to L1.
- `llm_defaults` (object) — snapshot of `GET /pipeline` defaults. Informational; do not repurpose.
- `resolved_schemas` (object) — JSON-Schema-by-version map for node outputs.
- `resolved_prompts` (object) — prompt-template-by-version map.

### `campaign.json`

Campaign knobs + scoring + optimizer LLM. Validated by
`promptpotter/application/config.py::CampaignConfig` with
`extra="forbid"` — unknown keys raise at boot. See `CampaignConfig`
for the full field list (every required and optional knob lives there
as a Pydantic `Field`).

Required per-dataset fields (no defaults — Pydantic raises if missing):

- `optimization.improvement_threshold` (float)
- `optimization.max_failures` (int)
- `optimization.degradation_threshold` (float)

System invariants — present with defaults, MUST NOT appear in any
`campaign.json`:

- `optimization.enable_l2 = True`
- `optimization.enable_l3 = True`
- `exploration.swap_out_delta_se = 0.7`

### `prompts/{node}.json`

Canonical 8-field `PromptTemplate` JSON per node. Loaded by
`application/datasets.py`. Schema:
`promptpotter/domain/opt_search_point.py::PromptTemplate`.

### `task_description.md`

Free-form markdown. Decomposed at `init` into the `task_context` dict
on `OptSearchPoint`. Operator-edited.

### `dataset.md`

Operator-facing guide. Free-form. Not parsed.

### `scan_variants.json` (optional)

Per-dataset axis-mutation library for sensitivity scans.

---

## 4. DispatchHub INJECTIONS keys

The set of `{{slot}}` names available in any optimizer prompt. Defined
in `promptpotter/application/optimization/dispatch/hub/injections.py::INJECTIONS`.
Adding a slot is one entry; using a slot not in the dict is a load-time
KeyError via `validate_template`.

Current keys (Stable API v1):

| Slot | Kind | Description |
|---|---|---|
| `plan` | TRACE | L3's strategic plan text. Persistent until next L3 fire. |
| `l3_to_l2_note` | DIRECTIVE | Sticky L3→L2 pointer. Mounted only in L2's template. |
| `rendered_prompt` | TRACE | Current best searchpoint's compiled prompt body. |
| `pipeline_param_catalogue` | DERIVED | Per-node param menu + ≤4-value enum hint, plus available models. |
| `diagnostics` | DERIVED | Layer-agnostic round readout: STATUS header + RoundDiagnostics body. |
| `validation_failures` | MEASUREMENT | Wound 1: L1 parse-time validator failures. |
| `runtime_failures` | MEASUREMENT | Wound 2: DegradationCheck mid-eval failures. |
| `l2_guard_breaches` | MEASUREMENT | Wound 4: L2 post-parse guard outcomes; non-empty force-triggers L3. |
| `l3_guard_breaches` | MEASUREMENT | L3 post-parse guard outcomes; L3 sees own past breaches. |
| `task_context` | TRACE | Persistent task framing refined by L2; broadcast to all four prompts. |
| `critique` | TRACE | Compact view of the most recent L1_CRITIQUE output dict. |
| `l1_overrides` | TRACE | Current L1 runtime knobs (creativity, n_variants, etc.) as JSON. |
| `l1_signal_catalogue` | DERIVED | Sorted L1_POSSIBLE names L2 may use in l1_layout. |
| `axis_memory` | DERIVED | Cross-cycle axis-keyed digest from AxisIndex. |

Per-template extras (caller-supplied via `compile_prompt(**hub_dict, **extras)`):

- `l1_generate`: `{n_variants}`
- `l1_critique`, `l2_context`, `l3_plan`: `{}` (no extras)
- `restructure`: `{consultation_instruction}`

---

## 5. CLI flags — `optimize`

Entry: `python -m promptpotter optimize`. The single write verb. Stable
flag set (Stable API v1):

| Flag | Meaning |
|---|---|
| `--config <path>` | Fresh mint: load `<path>` as `campaign.json`, mint new session+cycle. |
| `--dataset-name <name>` | Fresh mint by dataset directory name (alternative to `--config`). |
| `--from <N>` | Resume rewind: archive rounds > N and resume from round N+1. |
| `--no-divergence-check` | Resume only: skip the rescore-and-replay divergence check at boot. |
| `--fork-on-divergence` | Resume only: on divergence, mint a sibling cycle rooted at the divergence point. |
| `--sweep` | Sweep mode: round 1 scored, round 2 generation-only. Mints siblings under `sweeps/`. |
| `--diag` | Diag mode: round 1 scored, force L2 on round-1 evidence, round 2 generation-only. |
| `--halt-at-accuracy <float>` | Halt with `TARGET_HIT` once `best_accuracy ≥ X`. |
| `--max-spend-usd <float>` | Halt with `MAX_SPEND` once cycle spend ≥ X. |

Mutual exclusions:

- `--from`, `--no-divergence-check`, `--fork-on-divergence` are
  **rejected** when combined with `--config` or `--dataset-name`
  (resume-only flags cannot pair with a fresh mint).
- `--sweep` and `--diag` are mutually exclusive.

Resume-vs-mint dispatch: presence of `--config` or `--dataset-name`
means fresh mint; absence means "resume the active session pointer."

Other commands (`init`, `report`, `inspect`) have their own flag sets —
see `promptpotter/presentation/cli/parsers.py`. They are not part of
this version of the stable API (M11 still touches them).

---

## 6. Ledger event types

`events.jsonl` lines are typed records from
`promptpotter/domain/run_records.py`. Stable record types:

- **`PhaseRecord`** — phase enter/exit + round-boundary events.
  Fields: `record_type="phase"`, `phase`, `event`, `round`, `payload`.
- **`SnapshotRecord`** — per-sample / per-candidate snapshots inside a
  round. Fields: `record_type="snapshot"`, `event`, `round`,
  `candidate_idx`, `candidate_total`, `sample_idx`, `sample_total`,
  `payload`.
- **`ResumeCheckpointRecord`** — replayed-vs-archival decision rows
  feeding resume divergence checks. Fields: `record_type="checkpoint"`,
  `round`, `kind` (`ResumeCheckpointKind`), `payload`.
- **`TokenUsageRecord`** — optimizer LLM token rollups for spend.
  Fields: `record_type="token_usage"`, `model`, `provider`,
  `input_tokens`, `output_tokens`, `usd_cost`, `round`, `node`.
- **`LLMCallStartRecord`** — paired in-flight marker for live
  `dashboard.json::in_flight`. Fields: `record_type="llm_call_start"`,
  `call_id`, `node`, `model`, `round`, `candidate_idx`, `started_at_ms`.
- **`LLMCallRecord`** — paired LLM-call audit record. Fields: `record_type="llm_call"`,
  `call_id`, `node`, `model`, `round`, `prompt`, `response`, `parsed`,
  `usage`, `latency_ms`.

Forks within a family share one event stream via
`CycleEventLog.inherit_from(parent_offset)`. The forked cycle's
`events.jsonl` starts with the parent's records up to `parent_offset`
plus a `ResumeCheckpointRecord` of kind `FORK_CUT`.

Subscribers (projections) read records via
`DerivedView.on_record(record)`. Subscribers MUST NOT write any
campaign artifact beyond their declared allowlist —
`tests/test_invariants.py::test_no_direct_artifact_writes_outside_stores`
enforces this.

---

## 7. Per-cycle artifact paths

Operator-visible files inside `campaigns/{cycle_id}/`. The webapp +
downstream tooling read these directly; they are contract.

| Path | Writer | Description |
|---|---|---|
| `index.json` | `CampaignStore.create` / `update` | Campaign metadata: id, parent_session_id, dataset_name, backend_id, n_rounds, best_accuracy, rounds[]. Top-level summary. |
| `log.md` | `presentation/writers.py::write_log_md` | Markdown digest of every closed round + forks + hard samples + final winner. |
| `review.md` | `presentation/writers.py::write_review_md` | Per-round behavior-check + L1Stats narrative. |
| `rounds/round_NNNN.json` | `CampaignStore.save_round_file` | Full per-round detail: candidate scores, evaluators, prompt_fields, pipeline_params, OSP snapshot, decisions. |
| `dashboard.json` (at family root) | `LiveDashboardView._persist` | Live operator view; rewritten on every record. Refresh: 2 s. |
| `output.log` (at family root) | `LiveDashboardView._persist` (indirectly) | ANSI-colored line stream; CLI narration tail. Refresh: 1 s. |
| `langfuse/*.json` | `infrastructure/tracing/langfuse_push.py` | Per-cycle Langfuse export snapshots. |
| `prompts/{node}.json` | `infrastructure/tracing/langfuse_push.py` (and CLI `init`) | Resolved prompt templates for this cycle's runs. |
| `.runtime/ledger.jsonl` | `CycleEventLog.append` | The sole-ingress event log. Internal-but-stable shape (see §6). |
| `.runtime/cache/rounds/round_NNNN.json` | `AuditTrailView.flush` | Per-round audit cache (writer-buffered until round close). |
| `.runtime/cache/candidates/round_NNNN.json` | `CampaignStore.save_round_candidates` | Mid-round candidates checkpoint (deleted after L1 score on escalation). |
| `.runtime/streams/round_NNNN_p_best.jsonl` | `PoBBStreamView._handle_snapshot` | Per-sample P(best) trajectory. |
| `.runtime/stop.flag` | `presentation/api/routers/active.py` `POST /stop` | Operator stop signal; consumed by `session.stop_check`. |
| `.runtime/archived/resumed_at_<ts>/` | `CampaignStore.rewind_to_round` | Rewound rounds + candidates moved here on `--from N`. |

Sibling cycles (forks, diag, sweeps) live under `forks/`, `diag/`,
`sweeps/<batch>/forks/` within the family root. Each carries its own
copy of the per-cycle artifacts above; `dashboard.json` + `output.log`
are family-root-only (shared across forks).

The `.runtime/` directory is sanctioned as internal — its specific
file shapes can change between minor versions. The `rounds/round_NNNN.json`
public tree and `index.json` / `log.md` are the contract for any tool
reading per-cycle results.

---

## 8. What is NOT stable

Explicit non-promises:

- Internal module structure beyond §1–§7. The dispatch hub split into
  `hub/{bundle, injections, facade, builder}` is internal — only the
  public symbols (`DispatchHub`, `INJECTIONS`, `build_bundle`,
  `validate_template`) are stable.
- Private types (`_Injection`, `_TEMPLATE_EXTRAS`, etc.).
- Runtime dataclass shapes that don't appear in §1–§7 (`CycleSlice`,
  `RoundDigest`, `InjectionBundle`, `LiveStateCore`, etc. are
  implementation detail).
- In-memory caches and their invalidation strategies.
- The prompt templates themselves (`datasets/_optimizer/pipeline.json::resolved_prompts`).
  They are data, intentionally tunable. Forks may edit them; we may
  also edit them on any release.
- Test helpers (`tests/_helpers.py` contents).
- The `webapp/` layout. M11 surface + M12 control plane are still
  iterating.
