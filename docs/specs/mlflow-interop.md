# MLflow interop — what is left between the two tools

Nothing ships today: the MLflow client and sink left with `infrastructure/tracing/`, and a sink returns as a
projection on the ledger's roster ([`../architecture.md`](../architecture.md) § Tracing). This page is the
rest, in build order; a step that ships is deleted. MLflow is the operator's server and never our
dependency beyond the client (`mlflow-skinny`: full `mlflow` pins `cryptography<50`); no alias is set or
moved, nothing here hosts, routes or swaps ([`roadmap.md`](roadmap.md) § What PromptPotter emits). Verify
against a real `mlflow server` on a short path, never the offline run.

## 1. A campaign opts in

*Where* is the box's (`MLFLOW_TRACKING_URI`); *whether* becomes the campaign's: one config field, off by
default, set at launch or check-in from all five entry points; a sandbox cycle never registers. **Closes when** a box with the server set mirrors
nothing until a campaign opts in, and an opted-in self-optimization run leaves only its own cycle there.

## 2. The model on the version, in MLflow's own field

`register_prompt` takes a `model_config` (`PromptModelConfig`), which MLflow's UI renders; each prompt
registers per node, so that node's config goes
there and `promptpotter.tuned_params` keeps every prompt-less node. **Closes when** a registered version
shows the winning model for a one- and a two-node pipeline.

## 3. A campaign that starts from a registry prompt, and answers under its name

The channel exists: `CycleSeed.origin_prompts`, set from `origin_override` in
`application/jobs/mint.py::resolve_cycle_plan`, resolved first in
`application/origin.py::resolve_origin_opt_search_point`.

- **Fetched once, at mint, pinned in the seed** — a moved alias cannot change a running campaign's identity.
- **A new `OriginSource` member** with its sentence in `application/origin.py::_SEED_ORIGIN_LINEAGE`; the
  seed records the prompt's name and version.
- **One string into named fields**: a foreign prompt lands whole in `instruction`; a version *we* wrote
  restores exactly from a `promptpotter.prompt_fields` tag holding that node's `PromptExport.prompts` entry.
- **The winner registers under the seed's prompt name**, carried on the export. `{{name}}` is both the
  backend's slot and MLflow's.
- **Parity:** `origin_override` becomes one typed field on `MintCampaignPayload` / `FreshMint` and
  `EditDraftPatch`; an arm keeps refusing an origin override.
- **Open:** whether a restored origin carries its node's `shots`.

**Closes when** `new <dataset>` with a registry origin runs and its winner is version N+1 of that prompt.

## 4. Say how the text is delivered

Connectors deliver the registered text differently (`termnorm` system message, `lca-termnorm` fills
`{{query}}`, `harbor` a `SKILL.md` body, `dspy` signature instructions). One tag, `promptpotter.delivery`,
holds the campaign's `backend_type`; beside it, where a node declares an `output_schema`, that the backend
appends a structure directive the export does not hold.

## 5. Cells as traces, scores as feedback

The per-query trace path (`QueryScoreStart`, `QueryNodeSpan`, `QueryScoreEnd`, `TracingProjection.emit`)
was dead and is deleted.
<!-- probe: git grep -cE "QueryScoreStart" -- promptpotter == 0 -->

**Build it as a fold** ([`../architecture.md`](../architecture.md) § Tracing): the bridge feeds cells from
the records the bench already writes and `emit` goes. `MlflowClient.start_trace(..., experiment_id=,
run_id=)` / `end_trace` per cell, the grade as `mlflow.log_feedback`, `link_prompt_versions_to_trace` once
the winner registers. Step 1 bounds the volume.

## 6. PromptPotter inside MLflow's own optimizer call

`mlflow.genai.optimize_prompts(optimizer=…)` takes any `BasePromptOptimizer`; a subclass makes
PromptPotter one, an extra beside `presentation/teleprompter.py`
([`decisions.md` § 0006](../decisions.md#0006--embeddable-core-and-extras)). The DSPy adapter is the
template; MLflow's `eval_fn` is the connector and the caller's scorers are the grade.

## 7. MLflow's optimizers on the bench

GEPA is already here. **The meta-prompt optimizer is the only new method**: one proposing `llm` member over
the shared measurement ([`roadmap.md`](roadmap.md) § The bench). Low priority.

## 8. A box that keeps nothing

Wherever a mount is allowed, the operator's own volume mounted as `PROMPTPOTTER_HOME` needs no code. A
push/pull mode over a campaign's folder waits for a host that cannot mount; its bar: an S3-API object store
and the bucket behind a team's MLflow server, one implementation, moving between them changing an address
and a credential, never a code path.
