# connectors/ — backend-specific hook bundles

**A built-in connector is one module here defining `CONNECTOR`, a `Connector(...)` binding (`protocol.py`), never a `register()` call** <!-- probe: git grep -cE "\bregister\(" -- promptpotter/connectors/*.py == 0 -->. `promptpotter` lives in `application/runner/inner/spawn.py`; `llm_only` is a NODE on a `termnorm` pipeline, not a connector.

**Core never infers what a connector should declare**: what the next connector must reach into core to fix becomes a declaration on `Connector` or a fact derived from `extract_experiment`'s return, never a branch at the symptom site. Backend tunables ride [`datasets/CLAUDE.md`](../assets/datasets/CLAUDE.md) § Sole route. **Screen a new instrument before funding it** (`budget: {rounds: 0}`): read the spread, not the mean; a cost term needs a MEASURED anchor.

## TermNorm is not a third party

A bug whose cause sits in TermNorm's code is fixed in TermNorm, the one exception to "backends are read-only"; touching one side of the PP↔TermNorm highway means fixing both ([`backend-integration.md`](../../docs/operations/backend-integration.md) § Debugging the highway).

## Execution mode — declared, never name-branched

- **`in_process_run` is handed the `Sample`, and core clocks it**: no connector stamps `total_time` / `step_timings`. <!-- probe: git grep -cE "total_time|step_timings" -- promptpotter/connectors/*.py == 0 -->
- A remote backend off the `/matches` wire declares how it differs (`cell_path`, `read_reply`, `round_trip_is_latency`, `pipeline_declaration`), never a branch in `BackendClient`.
- Per-run state is the `workload` argument, never a ContextVar or a module cache. <!-- probe: git grep -cE "ContextVar\(|functools\.(lru_)?cache|^_[A-Z_]+: set\[" -- promptpotter/connectors/*.py == 0 -->

## The answer shape — declared, never inferred

A **ranked-label** connector yields `ground_truth: "<label>"`; a **verifier-graded** one yields `ground_truth: None` and is read off a `required_observation_keys` entry (`env_reward`). Ask `domain/scoring.py::is_verifier_graded`, never `predicted == NO_RESULT` <!-- probe: git grep -cE "predicted == NO_RESULT" -- promptpotter/*.py <= 3 -->. On a verifier-graded backend never invent a ranking or declare the node a `RANKER`. `Connector.measured_unit` declares what one row IS (`sample` | `cell`): no surface spells the literal beside a count (`domain/connector.py::unit_count`), and nothing caps on measured counts, which jitter.

## A connector is trusted code, not sandboxed

Loading one imports it into this process with the API keys, tenant tree and identity store in reach; capability scoping ([`decisions.md`](../../docs/decisions.md) § 0005) governs API principals, not in-process code. The table completes at [`application/CLAUDE.md`](../application/CLAUDE.md) § Registries complete at import, never at module scope.

## The credential rides the connector

`Connector.auth_token()`, `auth_scheme()` and `endpoint` are operator config, and an unset `endpoint` refuses every client (`Connector.unconfigured`), so nothing a request, flag or campaign sets can aim the token elsewhere. <!-- probe: git grep -cE "Authorization" -- promptpotter/connectors/*.py == 0 --> A check-in names only a connector with `serves_uploads`.

## Conventions

- An optional import is declared on `Connector.extra`; no connector checks `installed()`. <!-- probe: git grep -cE "installed\(" -- promptpotter/connectors/*.py == 0 --> Wire adapters are pure `(sample, PipelineParams) -> dict`; `identity_config` never reaches one.
- Where what a connector injects is not what the model consumes, arrival is MEASURED (`Connector.prompt_delivery`), never assumed.
- An `owns_panel` connector's panel is read before the row ladder, never as a fallback; cells key by `Sample.key`, so a panel reorder moves nothing.
- **A connector's own per-cell vocabulary is `Connector.cell_terms`**, and core `domain/cell_terms.py::CELL_TERMS` holds no connector's term. <!-- probe: git grep -cE "L4:" -- promptpotter/domain/cell_terms.py == 0 --> **A name has one meaning and one owner** (`scoring/evaluators.py::cell_name_owners`; two owners refuse the import).
- Declare every always-present payload key in `Connector.required_observation_keys`: an undeclared one is dropped silently; a verifier-graded cell says WHY it scored on `pipeline_data.outcome_note`.
