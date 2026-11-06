# connectors/ — backend-specific hook bundles

**A built-in connector is one module here defining `CONNECTOR`, a `Connector(...)` binding (`protocol.py`), never a `register()` call** <!-- probe: git grep -cE "\bregister\(" -- promptpotter/connectors/*.py == 0 -->; `_validate` refuses the rest. `promptpotter` lives in `application/runner/inner/connector.py` (`__init__.py::_APPLICATION_BUILT_INS`); `llm_only` is a NODE on a `termnorm` pipeline, not a connector.

**Core never infers what a connector should declare**: what the next connector must reach into core to fix becomes a declaration on `Connector` or a fact derived from `extract_experiment`'s return, never a branch at the symptom site. Backend tunables ride the dataset overlay — [`datasets/CLAUDE.md`](../assets/datasets/CLAUDE.md) § Sole route.

## TermNorm is not a third party

A bug whose cause sits in TermNorm's code is fixed in TermNorm — the one exception to "backends are read-only"; cross-repo edits are authorized, and touching one side of the PP↔TermNorm highway means fixing both. Per-dataset config still rides the overlay ([`backend-integration.md`](../../docs/operations/backend-integration.md) § Debugging the highway).

## Execution mode — declared, never name-branched

- **`in_process_run` is handed the `Sample`, and core clocks it** (`BackendClient._in_process_cell`): no connector stamps `total_time` / `step_timings`. <!-- probe: git grep -cE "total_time|step_timings" -- promptpotter/connectors/*.py == 0 -->
- A remote backend off the `/matches` wire declares how it differs (`cell_path`, `read_reply`, `round_trip_is_latency`, `pipeline_declaration`), never a branch in `BackendClient`.
- Per-run state is the `workload` argument (`InProcessWorkload`), never a ContextVar or a module cache. <!-- probe: git grep -cE "ContextVar\(" -- promptpotter/connectors/*.py == 0 -->

## The answer shape — declared in `extract_experiment`, never inferred

A **ranked-label** connector yields `ground_truth: "<label>"`; a **verifier-graded** one yields `ground_truth: None` and is read off a `required_observation_keys` entry (`env_reward`). Ask `domain/scoring.py::is_verifier_graded` / `all_verifier_graded`, never `predicted == NO_RESULT` <!-- probe: git grep -cE "predicted == NO_RESULT" -- promptpotter/*.py <= 3 -->; `Connector.answer_key` is where the answer TEXT lives. On a verifier-graded backend never invent a ranking or declare the node a `RANKER` (it banks a `0.0` `candidate_recall`).

## The measured unit — declared, never sniffed

`Connector.measured_unit` declares what one row IS (`sample` | `cell`); no surface holds a literal `"cell"` / `"sample"` beside a count — use `unit_plural` / `unit_count`. A per-cell fact decides nothing about the round, and nothing caps on measured counts — they jitter.

## A connector is trusted code, not sandboxed

Loading one imports it into this process with the API keys, tenant tree and identity store in reach; capability scoping ([`decisions.md`](../../docs/decisions.md) § 0005) governs API principals, not in-process code. The table completes at [`initialization/CLAUDE.md`](../application/initialization/CLAUDE.md) § Registries complete, never at module scope.

## The credential rides the connector

`Connector.auth_token()`, `auth_scheme()` and `endpoint()` are operator config, so nothing a request, flag or campaign sets can aim the token elsewhere; `protocol.py::Connector.credential_header` builds the header, for the wire and the backends listing alike. <!-- probe: git grep -cE "Authorization" -- promptpotter/connectors/*.py == 0 --> A check-in names only a connector with `serves_uploads` (`draft_patch.py`).

## Conventions

- An optional import is declared on `Connector.extra`; no connector checks `installed()`. <!-- probe: git grep -cE "installed\(" -- promptpotter/connectors/*.py == 0 -->
- Wire adapters are pure `(sample, pipeline_params) -> dict`; `identity_config` never reaches one.
- Where what a connector injects is not what the model consumes, arrival is MEASURED (`Connector.prompt_delivery`), never assumed.
- An `owns_panel` connector's panel is read by `dataset_access.py::dataset_experiment`, before the row ladder, never as a fallback; L4's `runner/inner/` reads it off the workload. Cells key by `Sample.key`, so a panel reorder moves nothing.
- **A connector's own per-cell vocabulary is `Connector.cell_terms`**, the declaration is the gate: ingress keeps only the keys it names (`cell_terms` plus `reported_keys`) as the row's `PipelineData.cell_report`, which channels, the formula namespace and dials read (`compiler.py::campaign_terms`); served on `CampaignPipelineResponse.cell_terms`; the core `domain/cell_terms.py::CELL_TERMS` holds no connector's term. **A name has one meaning and one value**: every name the cell namespace binds has ONE owner, the one serving its value (`scoring/evaluators.py::cell_name_owners`: reportable core terms, row-health terms, cell-bound names, matchers, `PipelineData` keys, turn scalars and `{step}_turns`, builtins, package evaluators; two owners holding a name refuse the import), and every door admitting a name reads it — a connector's terms and reported keys as the table completes (`wiring.py::complete_registries`; a reported key may name a reportable core term, whose channel it answers), a package evaluator at import, a campaign judge and a pipeline's observation keys at run init against the campaign's connector report. <!-- probe: git grep -cE "L4:" -- promptpotter/domain/cell_terms.py == 0 -->
- Declare every always-present payload key in `Connector.required_observation_keys` — an undeclared one is dropped silently; a verifier-graded cell says WHY it scored on `pipeline_data.outcome_note`.
