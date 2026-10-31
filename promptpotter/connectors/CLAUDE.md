# connectors/ — backend-specific hook bundles

**A built-in connector is one module here defining `CONNECTOR`, a `Connector(...)` binding (`protocol.py`), never a `register()` call.** <!-- probe: git grep -cE "\bregister\(" -- promptpotter/connectors/*.py == 0 --> `_validate` and `scripts/gate.py::_LAYERING` refuse the rest. Two are not where the roster suggests: **`promptpotter` lives in `application/runner/inner/connector.py`** (named in `promptpotter/connectors/__init__.py::_APPLICATION_BUILT_INS`; [`runner/inner/CLAUDE.md`](../application/runner/inner/CLAUDE.md)), and **`llm_only` is a NODE, never a connector** — it rides a `termnorm` pipeline.

**Core never infers what a connector should declare.** What the next connector must reach into core to fix becomes a declaration on `Connector` or a fact derived from what `extract_experiment` returns — never a branch at the symptom site; a connector shapes its `experiment_data` to fit the loader, never the reverse. **Backend tunables ride the dataset overlay** — owned by [`datasets/CLAUDE.md`](../assets/datasets/CLAUDE.md) § Sole route; this layer seeds it from `Connector.available_models`.

## TermNorm is not a third party

**A structural bug whose cause sits in TermNorm's code is fixed in TermNorm, never papered over here** — the one exception to "backends are read-only"; per-dataset config still rides the overlay. Its checkout is `termnorm_checkout` in `CLAUDE.local.md` (the backend is its `backend-api` folder).
**Cross-repo edits are authorized** (else coordinate with runfish5 on GitHub); the PP↔TermNorm highway is a shape contract — touch one side, fix both. Debugging: [`backend-integration.md`](../../docs/operations/backend-integration.md) § Debugging the highway.

## Execution mode — declared, never name-branched

**`BackendClient.run_query` dispatches on `Connector.execution` (`remote_http` | `in_process`)**, and `_validate` refuses a mismatched pairing. **`in_process_run` is handed the `Sample`, and core clocks it** (`BackendClient._in_process_cell`): the row is `sample.source_pin`, and no connector stamps `total_time` / `step_timings`. <!-- probe: git grep -cE "total_time|step_timings" -- promptpotter/connectors/*.py == 0 -->
**Per-run state is the `workload` argument (`InProcessWorkload`), never a ContextVar or a module cache** — harbor's one ContextVar scopes a single send's billing. <!-- probe: git grep -cE "ContextVar\(" -- promptpotter/connectors/*.py <= 1 -->

## The answer shape — declared in `extract_experiment`, never inferred

A **ranked-label** connector yields `ground_truth: "<label>"`, read by `label_match`; a **verifier-graded** one yields `ground_truth: None`, read off a `required_observation_keys` entry (`max(0.0, min(1.0, env_reward))`).
**Ask `domain/scoring.py::is_verifier_graded` / `all_verifier_graded`, never `predicted == NO_RESULT`**, which a dataset's `node_role` decides. <!-- probe: git grep -cE "predicted == NO_RESULT" -- promptpotter/*.py <= 3 --> `Connector.answer_key` is a different fact — where the answer TEXT lives.
**On a verifier-graded backend never invent a ranking or declare the node a `RANKER`** (it banks a `0.0` `candidate_recall`); a label-comparing formula raises at compile (`formula/compiler.py`).

## Injection is not consumption

**Ask of every new connector whether what it injects is what the model consumes**; where it is not, arrival is MEASURED through `protocol.py::Connector.prompt_delivery` (harbor's Agent Skill), never assumed.

## The measured unit — declared, never sniffed

**`Connector.measured_unit` declares what one row IS** (`sample`, or `cell`) and rides to `dashboard.json::measured_unit`.
**No surface holds a literal `"cell"` / `"sample"` beside a count, and no renderer infers it** — use `unit_plural` / `unit_count`. Exceptions keep their own word: the **evidence** surface (spans backends) and **ruler cells** (`AbilityReading.cells` / `DeltaRuler`).
**`cell` implies nothing about the run's control loop** — refuse a flag reasoning "a cell is expensive, therefore…".
**`cell_envelope_s` is the template: a per-cell fact that decides nothing about the round** (`application/scoring/cell_envelope.py`). Never cap on measured counts — they jitter.

## Registering a connector

**Nothing reads the table at module scope** — it completes at [`initialization/CLAUDE.md`](../application/initialization/CLAUDE.md) § Registries complete; a pre-spend check is `completion_check`. Another package declares the `promptpotter.connectors` entry point ([`stable-api.md`](../../docs/developer/stable-api.md) §1).

## A connector is trusted code, not sandboxed — and that is stated, not implied

**Loading a connector imports it into this process with the API keys, tenant tree and identity store in reach; installing one trusts its publisher completely.** Capability scoping ([`decisions.md`](../../docs/decisions.md) § 0005) governs API principals, not in-process code.
**`CONNECTORS.origins` is the audit surface**; a plugin shadowing a built-in, or a broken one, raises (`shared/plugin_registry.py::Registry`).

## The credential rides the connector

**`Connector.auth_token()` and `Connector.endpoint()` are operator config**, so nothing a request, flag or campaign sets can aim the bearer token elsewhere; no connector writes an `Authorization` header. <!-- probe: git grep -cE "Authorization" -- promptpotter/connectors/*.py == 0 -->
**A check-in names only a connector with `serves_uploads`** (`draft_patch.py`) — one whose cells read operator files or start containers is never a tenant's choice.

## Conventions

- **An optional import is declared on `Connector.extra`** — launch and the dataset listing refuse on it; no connector checks `installed()`. <!-- probe: git grep -cE "installed\(" -- promptpotter/connectors/*.py == 0 -->
- **Wire adapters are pure functions** `(query, pipeline_params) -> dict` — no I/O; `identity_config` never reaches one.
- **An `owns_panel` connector's panel is read by `infrastructure/store/dataset_access.py::dataset_experiment`**, ordered before the row ladder, never as a fallback; L4's `runner/inner/` reads it off the workload. Panel ORDER is the slot; the archive and δ key a cell by `Sample.key`, so a reorder moves nothing.
- **On an episodic backend `query` is an ID** — a real question rides `Sample.question`, which is what a judge sees.
- **Declare every always-present payload key in `Connector.required_observation_keys`** — an undeclared one is dropped silently.
- **A verifier-graded cell says WHY it scored on `pipeline_data.outcome_note`** — one line in the environment's words, absent where it gave none.
