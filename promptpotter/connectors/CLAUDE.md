# connectors/ — backend-specific hook bundles

**A connector is one file here exporting a `Connector(...)` binding (`protocol.py`) as `CONNECTOR`.**
Model/provider switches go in `dataset.yaml` `pipeline.nodes.{name}.config`, seeded from `Connector.available_models` — never in the backend's repo.

**Core never infers what a connector should declare.** Whatever the next connector must reach into core to fix becomes a
declaration on `Connector` or a fact derived from what `extract_experiment` returns — never a branch at the symptom site.

## Registered connectors

| Name | File | Wire shape | Use |
|---|---|---|---|
| `termnorm` | `termnorm.py` | `{query, steps, node_config}` → `/matches`; `POST /sessions` handshake | TermNorm production backend |
| `promptpotter` | `application/runner/inner/connector.py` (named in `__init__.py::_APPLICATION_BUILT_INS`; rules in [`runner/inner/CLAUDE.md`](../application/runner/inner/CLAUDE.md)) | `{query, optimizer_prompt_overrides}` → in-process inner cycle | L4 |
| `dspy` | `dspy_module.py` | `{query, prompt, params}` → the caller's `dspy.Module` | PromptPotter as a DSPy `Teleprompter` |
| `harbor` | `harbor.py` | `{query, prompt, model, provider, agent_kwargs}` → one containerized Harbor trial | Sandboxed agent, graded by the task's verifier |
| `dbllmbench` | `dbllmbench.py` | `{query, prompt, config}` → one `db-llm-bench` question in a container | Query-writing benchmark graded by its harness |

**`import dspy` is function-local here** — the table imports every built-in, and `[dspy]` is an extra; `presentation/teleprompter.py` imports it at module level.
**`llm_only` is a NODE name, never a connector** — it rides a `termnorm` pipeline over HTTP.
**A new connector shapes its `experiment_data` to fit the loader, never the reverse** (`extract_experiment` is the impedance-match seam).

## TermNorm is not a third party

**A structural bug whose cause sits in TermNorm's code is fixed in TermNorm, never papered over here** — the one exception to "backends are read-only"; per-dataset config still rides the overlay. Its checkout is `termnorm_checkout` in `CLAUDE.local.md` (the backend is its `backend-api` folder).
**Cross-repo edits are authorized** (else coordinate with runfish5 on GitHub); the PP↔TermNorm highway is a shape contract — touch one side, fix both. Debugging: [`backend-integration.md`](../../docs/operations/backend-integration.md) § Debugging the highway.

## Execution mode — declared, never name-branched

**`BackendClient.run_query` dispatches on `Connector.execution` (`remote_http` | `in_process`), never on the name** — `__init__.py::_validate` enforces the pairing.
**`in_process_run` is handed the `Sample`, and core clocks it** (`BackendClient._in_process_cell`): the row is `sample.source_pin`, and no connector stamps `total_time` / `step_timings`. `in_process` means no HTTP, nothing else.
**Per-run state is the `workload` argument (`InProcessWorkload`), never a ContextVar or a module cache.**

- **`harbor`** (operator-kept layer):
  - **The candidate prompt ships as ONE Agent Skill per episode with a FIXED frontmatter `description`**, body within the spec's limits ([`external-constraints.md`](../../docs/research/external-constraints.md) § SKILL).
  - **`nodes.agent.config.skill_delivery: system_prompt` is the second channel** — fixed per campaign, never in `param_keys`; absent means the Agent Skill.
  - **The panel is the pinned roster file** written by `pin-roster`, which refuses to overwrite a moved pin; no read fetches the registry. A local task's `path` is relative to its panel's directory; the panel's `agent.kwargs` may set no key `nodes.agent.config` owns.
  - **Trial scratch goes to the system temp dir; anything durable goes to the measurement archive.**
  - **Never sweep the task images, the package cache, or any container not naming our compose overlay** (the Docker host has other tenants); leftovers are reaped by `infrastructure/docker_host.py::reap_dead_producers`.
  - **A trial that measured the machine is never a cell** — `_infrastructure_failure` retries then raises `CellInfrastructureError` (`CellSendRefusedError` when out of credit); a throttled provider raises `CellThrottledError` to the run's backpressure.
  - Package cache: [`package-cache.md`](../../docs/operations/package-cache.md).
- **`dbllmbench`** — the image (pinned upstream commit, `resources/dbllmbench.Dockerfile`) carries the dataset's assets, so its tag is measurement identity; `_check_image` refuses a mismatched build.
  **The cell is billed off the harness's reported tokens** (`_spent`). **One execution is read at every retry budget** (`retry_levels`) — never sum across them.
  A harness without a full verdict set measured its provider or database (`_failure`), never the prompt. The database location rides `DBLLMBENCH_DB_*`.

## The answer shape — declared in `extract_experiment`, never inferred

| shape | `extract_experiment` yields | the formula reads |
|---|---|---|
| **ranked-label** (`termnorm`, `dspy`) | `ground_truth: "<label>"` | `label_match(predicted, ground_truth)` |
| **verifier-graded** (`harbor`, `promptpotter`, `dbllmbench`) | `ground_truth: None` | a `required_observation_keys` entry, e.g. `max(0.0, min(1.0, env_reward))` |

**`domain/scoring.py::is_verifier_graded` / `all_verifier_graded` are the ONE place asked** — never `predicted == NO_RESULT`, which a dataset's `node_role` decides.
**No second declaration (no `Connector` flag beside it).** `Connector.answer_key` is a different fact — where the answer TEXT lives — so a text reader never gets `NO_RESULT`.
**On a verifier-graded backend: never invent a ranking, never declare the node a `RANKER`** (it banks a `0.0` `candidate_recall`), and emit absence, not zero — owned by [`conventions.md`](../../docs/developer/conventions.md) § Code shape. A label-comparing formula is refused at compile (`formula/compiler.py`).

## A multi-turn cell — the turns are the backend's, the steps are the task's

(Operator-kept layer.) `pipeline_data::turns` is `domain/scoring.py::TurnRecord`.
- **Project a published turn format (Harbor's ATIF), never author one; parse it as plain JSON** so an upstream field degrades the record instead of raising in a paid cell.
- **A turn carries the STEP it served and never becomes one** — steps are the task's named segments; per-step rewards ride as TERMS beside the cell's aggregate. A turn ordinal is not an item.
- **No `turns` key = no turn concept; `[]` = none happened** — keep them distinct.
- **`reasoning_trace` holds what the agent DECIDED** — drop `user` turns and environment output when building it.

## Injection is not consumption — one backend, and the absence elsewhere is DECLARED

**Harbor's Agent Skill reaches the model only if opened, so arrival is MEASURED** (`protocol.py::Connector.prompt_delivery`; `harbor.py::_skill_opened` / `_skill_in_first_request` fill `SKILL_KEY`).
**No core `turn_scalars` member for it** — it would be constant `1.0` elsewhere. Ask of every new connector whether what it injects is what the model consumes.
**A per-step aggregate that drops a step with no verifier result is refused** (`harbor.py::_unscoreable_step` raises).

## The measured unit — declared, never sniffed

**`Connector.measured_unit` declares what one row IS** — `sample` by default, `cell` on `promptpotter` and `harbor` — and rides to `dashboard.json::measured_unit`.
**No surface holds a literal `"cell"` / `"sample"` beside a count, and no renderer infers it** — use `unit_plural` / `unit_count`.
Exceptions keep their own word: the **evidence** surface (spans backends) and **ruler cells** (`AbilityReading.cells` / `DeltaRuler`).
**`cell` implies nothing about the run's control loop** — refuse a flag reasoning "a cell is expensive, therefore…".
**`cell_envelope_s` is the template: a per-cell fact that decides nothing about the round; every connector declares one, never `None`** (`application/scoring/cell_envelope.py`). Never cap on measured counts — they jitter.

## Registering a connector

**A built-in is a module here defining `CONNECTOR`, never a `register()` call**; application-machinery built-ins are named in `_APPLICATION_BUILT_INS`. `_validate` and `scripts/gate.py::_LAYERING` enforce the rest.
**Nothing reads the table at module scope** — it completes at [`initialization/CLAUDE.md`](../application/initialization/CLAUDE.md) § Registries complete; a pre-spend check is `completion_check`. Another package declares the `promptpotter.connectors` entry point ([`stable-api.md`](../../docs/developer/stable-api.md) §1).

## A connector is trusted code, not sandboxed — and that is stated, not implied

**Loading a connector imports it into this process with the API keys, tenant tree and identity store in reach; installing one trusts its publisher completely.** Capability scoping ([`decisions.md`](../../docs/decisions.md) § 0005) governs API principals, not in-process code.
**`CONNECTORS.origins` is the audit surface.** No plugin shadows a built-in; a broken plugin is fatal (`shared/plugin_registry.py::Registry`).

## The credential rides the connector

**`Connector.auth_token()` is the ONLY route a bearer token reaches the wire, and `Connector.endpoint()` the ONLY source of its host** — both operator config, so nothing a request, flag or campaign sets can aim the token elsewhere. A `BackendClient` reads capabilities off the `Connector` it holds; the registry guard enforces the `in_process`/`remote_http` pairing.
**A check-in names only a connector with `serves_uploads`** (`draft_patch.py`) — one whose cells read operator files or start containers is never a tenant's choice.

## Conventions

- **An optional import is declared on `Connector.extra`** — launch and the dataset listing refuse on it; no connector checks `installed()`.
- **Wire adapters are pure functions** `(query, pipeline_params) -> dict` — no I/O; `identity_config` never reaches one.
- **An `owns_panel` connector's panel has ONE reader, `infrastructure/store/dataset_access.py::dataset_experiment`**, ordered before the row ladder, never a fallback; L4's `runner/inner/` reads it off the workload.
  Panel ORDER is the slot; the archive and δ key a cell by `Sample.key`, so a reorder moves nothing.
- **On an episodic backend `query` is an ID** — a real question rides `Sample.question`, the only channel a judge sees.
- **Declare every always-present payload key in `Connector.required_observation_keys`** — an undeclared one is dropped; `application/initialization/wiring.py::_verify_required_observation_keys` raises at init.
- **A verifier-graded cell says WHY it scored on `pipeline_data.outcome_note`** — one line in the environment's words, absent where it gave none.
- **Revision pinning is opt-in** (`expected_revision` + `version_check`); init WARNs on drift.
