# MLflow interop — what is left between the two tools

What exists is [`../operations/observability.md`](../operations/observability.md) § MLflow's: one server named by
`MLFLOW_TRACKING_URI`, rounds as runs, the winner as a prompt version linked to the run that crowned it. This page
is the rest, in build order; a step that ships is deleted.

- **MLflow is the operator's server and never our dependency beyond the client** (reasons beside `mlflow-skinny`
  in `pyproject.toml`).
- **No alias is set or moved, and nothing here hosts, routes or swaps** — [`roadmap.md`](roadmap.md) § What PromptPotter emits.
- **Verify against a real server, never the offline run** (`scripts/offline_run.py` answers every network edge
  in-process). Stand a throwaway `mlflow server` up in a separate environment on a short path (its migrations
  break past `MAX_PATH`) and drive `infrastructure/tracing/mlflow_server.py` with a real `export.json` and
  `tests/factories.py::round_result`.

## 1. A campaign opts in

**Decision.** *Where* is the box's: `MLFLOW_TRACKING_URI` names the server, unset means none. *Whether* is the
campaign's: one config field, off by default, chosen at launch or check-in. A sandbox cycle never inherits it, so
the sandbox check in `application/runner/output.py::publish_export` goes. The field rides step 3's carriers, so
all five entry points set it.

**Closes when** a box with the server set mirrors nothing until a campaign opts in, and an opted-in
self-optimization run leaves only its own cycle on the server.

## 2. The model on the version, in MLflow's own field

**Decision.** `register_prompt` takes a `model_config` (`PromptModelConfig`), which MLflow's UI renders. Each prompt
registers per node (`infrastructure/tracing/mlflow_server.py::register_export`), so that node's config goes there;
`promptpotter.tuned_params` keeps every prompt-less node and is omitted when there is none.

**Closes when** a registered version shows the winning model in the model-config panel for a one- and a two-node pipeline.

## 3. A campaign that starts from a registry prompt, and answers under its name

Their prompt in, the winner back as the next version of *their* prompt (today the name is
`{tenant_id}.{dataset_name}`). The channel exists: `CycleSeed.origin_prompts`, set from `origin_override` in
`application/jobs/mint.py::resolve_cycle_plan` and resolved first in
`application/origin.py::resolve_origin_opt_search_point`.

- **Fetched once, at mint, pinned in the seed** — a resume re-resolves from the seed
  (`application/jobs/mint.py::refuse_drifted_resume`), so a moved alias cannot change a running campaign's identity.
- **A new `OriginSource` member** with its sentence in `application/origin.py::_SEED_ORIGIN_LINEAGE` (its
  import-time assert fails without it); the seed records the prompt's name and version.
- **One string into named fields**: a foreign prompt lands whole in `instruction`; a version *we* wrote restores
  exactly from a `promptpotter.prompt_fields` tag holding that node's `PromptExport.prompts` entry.
- **The winner registers under the seed's prompt name**, carried on the export.
- **Placeholders need no conversion** — `{{name}}` is both the backend's slot (`OptSearchPoint.render`) and MLflow's.
- **Parity:** `origin_override` is accepted by `mint_framed_cycle` and `resolve_cycle_plan` but passed by neither
  `application/jobs/launcher/mint_and_start.py::hold_fresh_mint` nor `application/embedded_run.py::run_campaign`. It
  becomes one typed field on `MintCampaignPayload` / `FreshMint` and `EditDraftPatch`. An arm keeps refusing an
  origin override.
- **Open:** whether a restored origin carries its node's `shots` (a version we wrote renders them into the text).

**Closes when** `new <dataset>` with a registry origin runs and its winner is version N+1 of that prompt.

## 4. Say how the text is delivered

Connectors deliver the registered text differently (`termnorm` system message, `lca-termnorm` fills `{{query}}`,
`harbor` a `SKILL.md` body, `dspy` signature instructions). **Decision:** one tag, `promptpotter.delivery`, holding
the campaign's `backend_type` — not MLflow's chat template, since only one shape is a system/user pair. State
beside it: where a node declares an `output_schema`, the backend appends a structure directive the export does not hold.

## 5. Cells as traces, scores as feedback

**What is there is dead**: `tracing/events.py` declares `QueryScoreStart`, `QueryNodeSpan` and `QueryScoreEnd`,
`LangfuseSink` handles them, nothing constructs one, and `TracingProjection.emit` has no caller.
<!-- probe: git grep -cE "QueryScoreStart\(" -- promptpotter == 0 -->

**Decision: revive it as a fold.** A trace is a fold of the ledger ([`../architecture.md`](../architecture.md)
§ Tracing), so the bridge feeds cells from the records the bench already writes and `emit` goes; no member opens a
span. A round's run opens when the round is entered, so its traces attach: `MlflowClient.start_trace(...,
experiment_id=, run_id=)` / `end_trace` per cell, the grade as `mlflow.log_feedback`, and
`link_prompt_versions_to_trace` once the winner registers. Step 1 bounds the volume to opted-in campaigns.

## 6. PromptPotter inside MLflow's own optimizer call

`mlflow.genai.optimize_prompts(optimizer=…)` takes any `BasePromptOptimizer`; a subclass makes PromptPotter one.
It lives here as an extra beside `presentation/teleprompter.py`
([`decisions.md` § 0006](../decisions.md#0006--embeddable-core-and-extras)). The DSPy adapter is the template:
`PromptPotterOpt.acompile` writes a dataset directory, opens a session with the host's program as a connector
(`connectors/dspy_module.py`), runs the embedded campaign and applies the winner; MLflow's `eval_fn` is the
connector and the caller's scorers are the grade.

## 7. MLflow's optimizers on the bench

**GEPA is already here** — `GepaPromptOptimizer` delegates to the `gepa` package the `gepa` preset re-expresses;
MLflow runs the library's defaults where the preset pins the paper's. **The meta-prompt optimizer is the only new
method**: one proposing `llm` member over the shared measurement (a peer is decomposed, never called as a library —
[`roadmap.md`](roadmap.md) § The bench (M13)). Undecided: what `paper:` names for a vendor implementation,
and whether a manifest with no sampler or selector runs a round. Low priority. MLflow as a peer:
[`../research/landscape.md`](../research/landscape.md) § MLflow.

## 8. A box that keeps nothing

**The answer wherever a mount is allowed needs no code**: the operator's own volume mounted as `PROMPTPOTTER_HOME`.
A push/pull mode over a campaign's folder waits for a host that cannot mount. **The bar it is held to:** two hosts,
one implementation — an S3-API object store and the bucket behind a team's MLflow server — moving between them
changes an address and a credential, never a code path.
