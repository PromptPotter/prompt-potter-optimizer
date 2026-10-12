# MLflow interop — what is left between the two tools

What exists is [`../operations/observability.md`](../operations/observability.md) § MLflow's: one
server named by `MLFLOW_TRACKING_URI`, rounds as runs, the winner as a prompt version linked to the
run that crowned it. This page is the rest, in the order to build it. Each step states its
decision, the symbol it lands on, and what closes it. A step that ships is deleted from here.

Three rules hold for every step:

- **MLflow is the operator's server and never our dependency beyond the client.** The reasons are
  beside `mlflow-skinny` in `pyproject.toml`.
- **No alias is set or moved, and nothing here hosts, routes or swaps** —
  [`roadmap.md`](roadmap.md) § Application radius.
- **Verify against a real server, never the offline run.** `scripts/offline_run.py` answers every
  network edge in-process, so a client pointed at a server retries into a wall. Stand a throwaway
  `mlflow server` up in a separate environment, on a short path (its migrations break past
  `MAX_PATH`), and drive `infrastructure/tracing/mlflow_server.py` directly with a real
  `export.json` and `tests/factories.py::round_result`.

## 1. A campaign opts in

**Decision.** Two questions with two owners. *Where* is the box's: `MLFLOW_TRACKING_URI` names the
server, and unset means the box has none. *Whether* is the campaign's: one config field, off by
default, chosen at launch or in the check-in. The sink binds and the winner registers only for a
campaign that said yes.

**What it removes.** A sandbox cycle is minted by the recursion and never by the operator, so it
must never inherit the field. With that, the sandbox check in
`application/runner/output.py::publish_export` goes, and so does the question of whether a
sandbox's rounds are mirrored.

**Parity.** The field rides the carriers step 3 names for its origin source, so the CLI, REST, the
webapp check-in, the embedded launch and `/potter-run` all set it.

**Closes when** a box with the server set mirrors nothing until a campaign opts in, and a
self-optimization run of an opted-in campaign leaves only its own cycle on the server.

## 2. The model on the version, in MLflow's own field

**Decision.** `register_prompt` takes a `model_config` (`PromptModelConfig`: provider, model name,
temperature, max tokens, top-p, and `extra_params` for the rest), and MLflow's UI renders it. The
prompt node's config goes there; the `promptpotter.tuned_params` tag keeps every *other* node and
is omitted when there is none.

**What it needs first.** `PromptExport` does not say which node its prompt was rendered onto, and
`tuned_params` is keyed by every node. The node is `PipelineSchema.prompt_node_names()[0]`, the one
`OptSearchPoint.to_job_search_point` writes the prompt to. So `PromptExport` gains `prompt_node`,
stamped in `domain/export.py::build_prompt_export`, and `stable-api.md` § 5c names it.

**Closes when** a registered version shows the winning model in MLflow's model-config panel for a
one-node and a two-node pipeline.

## 3. A campaign that starts from a registry prompt, and answers under its name

This is the step that makes the round trip a team recognises: their prompt in, the winner back as
the next version of *their* prompt, where today the name is `{tenant_id}.{dataset_name}`.

**The channel exists.** A non-dataset origin already rides `CycleSeed.origin_prompt_fields`, set
from `origin_override` in `application/jobs/mint.py::_plan` and resolved first in
`application/origin.py::resolve_origin_opt_search_point`. Every mint path reaches `_plan`.

**Decisions.**

- **Fetched once, at mint, and pinned in the seed.** A resume re-resolves the origin from the seed
  (`mint.py::refuse_drifted_resume`); a live read there would let a moved alias change a running
  campaign's identity.
- **A new `OriginSource` member** for a registry origin, with its sentence in
  `application/origin.py::_SEED_ORIGIN_LINEAGE` — the import-time assert there fails without it.
  The seed also records the prompt's name and version, which no model carries today.
- **One string into six fields.** A registry prompt is one text and an origin is six named fields.
  A foreign prompt lands whole in `instruction`, the same fallback
  `DraftCampaign.committed_prompt_fields` uses for a bare task description. A version *we* wrote
  restores exactly: `register_export` gains a `promptpotter.prompt_fields` tag holding
  `PromptExport.prompt_fields`, and the reader prefers it.
- **The winner registers under the seed's prompt name** when the origin came from the registry,
  so `register_export` takes the name from the export, which therefore carries it.
- **Placeholders need no conversion.** A `{{name}}` left in a prompt is already the backend's slot
  (`OptSearchPoint.compile_prompt`), and that is MLflow's syntax too.

**Parity.** `origin_override` is accepted by `mint_framed_cycle` and `prepare_fresh_cycle` and
passed by neither `mint_and_start.py::hold_fresh_mint` nor `embedded_run.py::run_campaign`. The
source is one typed field on `MintCampaignPayload` / `FreshMint` and on `EditDraftPatch`, so the
CLI, REST, the webapp check-in, the embedded launch and `/potter-run` all name it. An arm refuses
an origin override and keeps refusing.

**Open.** A version we wrote renders its shots into the text; restoring from the fields tag drops
them, and whether a restored origin should carry `shot_ids` is undecided.

**Closes when** `new <dataset>` with a registry origin runs, and its winner is version N+1 of the
prompt it started from.

## 4. Say how the text is delivered

The registered text is what the prompt node received, and connectors deliver it differently: the
`termnorm` `llm_only` node sends it as the system message with the sample as the user message;
`lca-termnorm`'s nodes fill `{{query}}` inside it; `harbor` writes it as a `SKILL.md` body; `dspy`
sets it as every predictor's signature instructions. A reader of the registry cannot tell which.

**Decision.** One tag, `promptpotter.delivery`, holding the campaign's `backend_type`. Not MLflow's
chat-message template: only one of the four shapes is a system/user pair.

**Known gap to state beside it.** Where a node declares an `output_schema`, the backend appends a
structure directive to the system message that the export does not hold.

## 5. Cells as traces, scores as feedback

**What is there is dead.** `tracing/events.py` declares `QueryScoreStart`, `QueryNodeSpan` and
`QueryScoreEnd`, `LangfuseSink` handles them, and nothing constructs one: `TracingProjection.emit`
has no caller. Decide this before building on it — revive or delete, never leave.

**Decision.** Revive it as a fold. A trace is a fold of the ledger
([`../architecture.md`](../architecture.md) § Tracing), so the bridge feeds cells from the records
the bench already writes, exactly as it feeds rounds, and `emit` goes. Both sinks then receive
cells and no member opens a span.

**The MLflow half.** A round's run opens when the round is entered, not when it closes, so its
traces can attach: `MlflowClient.start_trace(..., experiment_id=, run_id=)` and `end_trace` per
cell, the grade as `mlflow.log_feedback(trace_id=, name=, value=)`, and
`link_prompt_versions_to_trace` once the winner is registered.

**Volume.** A cell per trace on every round. Step 1 bounds it to the campaigns that opted in.

## 6. PromptPotter inside MLflow's own optimizer call

`mlflow.genai.optimize_prompts(optimizer=…)` takes any `BasePromptOptimizer`; GEPA and a
meta-prompt optimizer are its built-ins. A subclass of ours makes PromptPotter one of them.

**Where it lives.** In this repo, as an extra, beside `presentation/teleprompter.py` —
[`../adr/0006-embeddable-core-and-extras.md`](../adr/0006-embeddable-core-and-extras.md): "An
embedded adapter lives here rather than in its own repo."

**Shape.** The DSPy adapter is the template: `PromptPotterOpt.acompile` writes a dataset directory,
opens a session with the host's program as a connector (`connectors/dspy_module.py`), runs the
embedded campaign and applies the winner. MLflow hands the optimizer an `eval_fn`, the train data
and the target prompts; `eval_fn` is the connector, and the caller's scorers are the grade.

## 7. MLflow's optimizers on the bench

**GEPA is already here.** `GepaPromptOptimizer` delegates to the `gepa` package, the method the
`gepa` preset re-expresses as members. It is no second peer; the difference is that MLflow runs
the library's defaults where the preset pins the paper's values.

**The meta-prompt optimizer is the only new method**, and it is small: one pass, one candidate, no
selection, re-scored on the rows it was shown. A peer is decomposed into members, never called as
a library ([`roadmap.md`](roadmap.md) § The optimizer plug point), so this is one proposing `llm`
member over the shared measurement. Two things are undecided: what a manifest's `paper:` names
for a vendor implementation, and whether a manifest with no sampler or selector runs a round.
Low priority: it measures a weak baseline, and item 8 comes first.

## 8. The landscape entry

`../research/landscape.md` has no MLflow entry. It gets one — tracking, the Prompt Registry, the
MCP server, `optimize_prompts` — written from MLflow's docs as
[`../research/CLAUDE.md`](../research/CLAUDE.md) requires. What was seen so far came from the
installed client and one tutorial video, which is not a reading.

## 9. The workspace as a cache — a box that keeps nothing

Wider than MLflow, and recorded here because the question came up beside it.

**Why.** Some hosts may not let a second service hold their data. The same mode serves a
container, which has no disk worth trusting.

**Decision.** The loop keeps reading and writing local files. The store is file-shaped throughout
— `infrastructure/store/stores.py::build_stores` hands every store a path, and file access is not
confined to that package — so a second backend behind the store's methods is a rewrite, and a
remote read inside the loop makes the remote load-bearing. The seam is the directory instead: a
campaign's folder is pushed to a remote at every round close and at run end, pulled before a
resume, and removed locally once the run has ended and the push is confirmed.

**Shape.** One protocol with two verbs, push and pull, over a campaign's folder; the remote is a
setting. The one implementation speaks the S3 API, which most object stores answer, so a second
host changes an address and a credential and no code. MLflow's own artifact API is a different
protocol: a team on MLflow names the bucket behind its server. Nothing in the loop, the ledger or
the views changes.

**Held until a host asks.** The mount below covers every host that allows one, and this mode adds
an object-store client as an extra, which has to be earned by a caller that cannot mount.

**Needs no code at all:** the operator's own volume mounted as `PROMPTPOTTER_HOME`. That is the
answer wherever a mount is allowed, and it is stated beside this mode, not replaced by it.

**Limits to state.** A run's folder exists locally while it runs. Prompts and samples still go to
the model provider. Accounts, spend and the trail are the box's own records and stay on it.

**Open.** Which reads outside a run — the campaign tree, the archive, lineage queries — need a
pulled folder, and whether the measurement archive, which spans campaigns, travels with them.

**The bar anything of this kind is held to.** Two hosts, one implementation: an object store that
answers the S3 API, and the bucket behind a team's MLflow server. Moving between them changes an
address and a credential. A design that needs a second code path for either is the wrong design.

**Closes when** a campaign runs in a container with an empty disk, the container is destroyed
mid-run, and a new one resumes it from the remote, losing at most one round — once against each of
those two hosts, with no code changed between them.
