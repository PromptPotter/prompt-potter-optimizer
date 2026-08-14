# Spec — `promptpotteropt`, the DSPy adapter

**Status: planned, nothing built.** The user-facing page this produces is
[`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md).

## The split, and why it is at the top

A **fourth distribution tier**. Tiers 1–3 are all *someone runs PromptPotter* — hosted,
local `/potter-run`, self-hosted team. This one is *PromptPotter runs inside someone else's
program*: a DSPy user who will never clone this repo or open the webapp. So it ships as a
**separate repo and PyPI name**, not a mode or a flag here.

**The dependency arrow points one way: `promptpotteropt` depends on
`promptpotter`; this package never imports `dspy`.** That is what makes "no new
dependency for existing installs" structural rather than a promise, and it forbids the
obvious shortcut — a stripped copy of the loop inside the adapter — because two loops
drift. One engine.

## What reading the code changed

The first draft of this spec listed six things to build. Four of them already exist here,
and looked absent only because the list was written against DSPy's inventory instead of
ours. Recorded so nobody rebuilds them:

| Was listed as "build" | Actually |
|---|---|
| A no-server execution path | `Connector.execution = "in_process"` is **shipped** and carries L4 today. `connectors/llm_only.py` does **not** exist — it was deleted for having no adopters, and [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) says do not re-add it. The adapter declares its own `in_process` connector. |
| Registering that connector | The `promptpotter.connectors` entry-point group is **published, validated at import, and stable API** ([`../developer/stable-api.md`](../developer/stable-api.md) §1). A connector shipped from another package touches nothing here. |
| Checkpoint and resume | The campaign tree, `pause`, `resume --from N` and `--fork-on-divergence` are ours, not DSPy's. An adapter that mints a real cycle gets all of it — the CLI ships in the same install. |
| A dollar ledger and ceiling | `BudgetGate` + `spend_budget_usd` + the live `.runtime/spend_cap.json` re-read already halt a run on spend. The adapter passes a number. |
| The param axes | `optimizer.param_keys` already evolves persona / thinking_style / temperature / `reasoning_effort` / model, and `PromptTemplate.render()` already flattens the result into one instruction string — which is the only shape a DSPy `Predict` can receive. |
| The terminal readout | `LiveDisplay` is a ledger subscriber that `build_run_observers(display=…)` binds, and the embedded launch entry takes one — so what a DSPy user watches during a compile is the CLI's readout, not a progress bar. Nothing to build; the competitive row was already won. |

**So the adapter is small, and the work that remains is mostly in this repo.** What is left
is naming, placement and stability of seams we already have — plus one genuine hazard
(below). That is why the phases run this way round.

## Phase A — independent, any order, nothing waits on them

1. **Correct the two docs.** This file's `llm_only` claim was wrong; the usage page's
   trade-away table over-claims what is lost, because an adapter that mints a real cycle
   keeps pause / resume / fork / replay / the diagnostic verbs. What a DSPy user actually
   trades away is the **webapp and the agent**, not the campaign tree. *(landed with this
   commit)*
2. **Re-tier core dependencies.** Moved to `[api]` (the seven web/identity packages) and
   `[excel]` (openpyxl); core is the nine the engine actually imports. The split was
   measured, not argued — `embedded_run` and the CLI entry each import to exactly that
   closure, and the whole `[api]` set is reachable only through `main.py` and
   `presentation/api/`. `[all]` folds both in, so `.[all,dev]` is unchanged.
   *(landed with this commit)*
3. **Link both docs** — the usage page from [`../developer/README.md`](../developer/README.md)
   and the root README table, this spec from [`CLAUDE.md`](CLAUDE.md). Not `docs/README.md`:
   that is a folder-level table with no per-doc rows. *(landed)*

## Phase B — close the gap, in this repo, no `dspy` import

Each item deletes adapter code by making a seam we already have supported and correctly
named. All four are net simplifications here, which is what makes them worth doing
regardless of whether the adapter ever ships.

1. **The library entry, named for its contents.** *(landed)*
   `presentation/views/notebook_run.py` was neither a view nor notebook-specific: it was the
   embedded launch entry, named after its first caller. Now `application/embedded_run.py`
   (`open_session` → `mint_and_score_origin` → `run_campaign`), with the read-out half split
   off to `presentation/views/completion.py` — a split the layer rule forces, since
   `application/` may not import `presentation/`. Two live defects fell out of the move, both
   in callers that sit outside every gate (`gate.py` runs mypy + ruff over `promptpotter/` and
   `tests/` only): the BBEH harness passed a `task_context=` the function never accepted, and
   two callers read `origin.origin_acc` off a `CampaignOrigin` that has no such field. **That is
   the argument for the rename, not a side-effect of it** — a seam whose consumers all live
   outside the gate perimeter is one nothing checks. It also closed the third Open item below:
   the harness's `stop_reason == "interrupted"` string compare is now `stop_reason_outcome`.
2. **Positional sample identity was a live hazard, not just an adapter problem.** *(landed)*
   `Sample.id` is an index (`fallback_id=i`) and the archive keys on `sample_id: int`, so the
   cache key is *position under a dataset name* — query text is not in it. Re-cut the rows under
   a name you already used and it served the old rows' scores, silently. The rule ("new rows, new
   name") was written down and enforced by nothing, and a DSPy trainset is an in-memory list a
   user reorders casually, so the hazard arrives with the first user.
   **The fix needed no fingerprint and no migration:** every stored measurement row already
   carries the `query` and `ground_truth` it was measured against, so the reuse path compares
   them against the row now sitting at that position and raises `DatasetIdentityError`
   (`_assert_measured_content_matches`, before the first backend call). Reusing what is already
   on disk beats a new field twice over — it catches ONE edited row where a whole-dataset
   fingerprint reports only the aggregate, and it applies to every measurement ever taken rather
   than to runs recorded after the change. Verified against the live store: 34,193 archived
   `justlogic-d234` rows agree with the current dataset, so nothing in flight is blocked.
   Its neighbour settled with it: `run_id` was `{label}_{content_hash[:8]}` — 32 bits, after
   `content_hash` was already cut to 24 — while 510 archived runs share the label `candidate_0`,
   so the prefix carried the whole discrimination. It is the full hash now.
3. **One supported config-override path.** *(landed)* `load_dataset_campaign_config(path,
   overrides=…)` merges a nested mapping onto the dataset's template **before** validation, so an
   unknown knob raises instead of being dropped, and the merge is depth-first — overriding
   `optimization.max_rounds` keeps every sibling knob. That is the seam `Loop(...)` maps onto.
   Both harnesses were hand-patching the parsed dict, which `read_campaign_config_file`'s own
   docstring already names as the anti-pattern, and both were broken by it: `campaign.yaml` is
   real YAML, so the BBEH harness's `json.loads` raised on its first line — the harness could not
   run at all — and `scripts/smoke_campaign.py` swallowed the same error behind a bare `except`,
   so no smoke run ever read a dataset's declared scoring formula.
4. **The export artifact.** *(landed)* `cycles/{id}/export.json` — shape, rules and the three
   consumers are owned by [`roadmap.md`](roadmap.md) § Application radius; the reader contract is
   [`../developer/stable-api.md`](../developer/stable-api.md) § 5c. This arc's stake was that it
   **replaces** the adapter's handoff question — the adapter reads an artifact instead of
   re-deriving a winner, and `PromptTemplate(**fields).render()` (what the BBEH harness hand-rolls
   today) is the loader's business now, not each caller's.
   **The hazard it turned up sits one layer in.** `CycleResult.winner_prompt_fields` is the
   wire-side projection: `to_job_search_point` flattens `few_shot_examples` into a rendered
   `few_shot_block`, which `from_prompt_fields` cannot restore and `extra="forbid"` rejects
   outright. Every caller re-deriving a winner from it — the adapter included — would have built
   a prompt missing its demonstrations, or crashed. The round document carries the structured
   dict, so the artifact projects the ROUND the composite high-water names, origin round included.
5. **Consolidate the two telemetry fan-outs.** ✅ The premise was that a fact reaching both the
   ledger and Langfuse costs eight synchronized edits. **Measured, the second four were being
   paid by facts that reached no remote sink at all**: five mid-round events (candidate created
   / scored, round winner, L1 critique, layer applied) routed to the local mirror alone, which
   nothing reads, restating what the ledger and `rounds/round_NNNN.json` already carried — so
   each cost its writer a second emit and bought nothing. They are deleted, and the rule is
   stated where the next one would be added: an `Event` needs a remote sink, a mid-round fact
   goes to the ledger ([`../developer/adding-a-surface.md`](../developer/adding-a-surface.md)
   §1). What remains of the tracing half is one routing table replacing the dispatch `match`,
   guarded at construction — so C4's bridge extends a registry rather than a ninth edit.

## Phase C — the adapter, in this repo ✅

**It is an extra, not a second distribution** — `pip install promptpotter[dspy]`, one name and
one version. The separate-repo plan was written before Phase B; what B1–B5 left was ~300 lines,
which does not carry its own CI, release pipeline and version matrix. It also *shrank* the work:
the connector is a row in `_BUILTIN` rather than a published entry-point registration, and
`expected_revision` / `version_check` — the cross-repo drift machinery — is needed for nothing.
The one cost that survives the move is release coupling: DSPy moves fast (this was read against
3.1.3 and built against 3.3.0), so a break there cuts a `promptpotter` release.

`import dspy` is **function-local in the connector** — `connectors/__init__.py` imports built-ins
eagerly, so a module-level import would break every install that did not ask for the extra — and
module-level in `presentation/teleprompter.py`, whose only importer is the caller, where it raises
naming the extra. Four parts, none of them a loop:

1. **A `dspy` connector** ✅ `connectors/dspy_module.py` — `execution: "in_process"`, one row in
   `_BUILTIN`. The caller's metric IS the scorer: its float rides the `dspy_score` observation
   key and the campaign formula reads that key, the same channel the L4 connector's proxies use,
   so no grading rule is restated on our side. The student and metric ride a ContextVar, because
   `in_process_run` is a module-level hook with no call-site state.
2. **`PromptPotterOpt(Teleprompter)`** ✅ `presentation/teleprompter.py` — the fifth entry point,
   read-only over `application/embedded_run.py` exactly as the CLI is. It reads the winner off
   `export.json` (§ B4 — never re-derives one) and applies it with `with_instructions()`.
   `valset` is accepted and named as unused rather than silently dropped. Its async peer
   `acompile()` is § The async seam.
3. **`Loop` / `Node`** ✅ — projected onto a materialized `pipeline.yaml` + `campaign.yaml` under
   the tenant dataset dir, which is what `dataset_name` then resolves. Rewritten each compile,
   because they are a projection of the arguments and not operator-authored config. **One node:**
   a DSPy program is a single call from here, so the prompt reaches every predictor — stated on
   the usage page, since it is wrong for a program whose predictors do different jobs.
4. **The observability bridge.** Narrower than first written. `BaseCallback` stops at
   `on_module_*` / `on_lm_*` / `on_adapter_{format,parse}_*` / `on_tool_*` / `on_evaluate_*`
   — **there is no compile-level hook**; `on_compile` appears nowhere in 3.1.3. So round and
   campaign lifecycle emission is ours, and `on_evaluate_start` / `on_evaluate_end` is the
   coarsest thing DSPy offers to hang a round on. `mlflow.dspy.autolog(log_compiles=True)`
   still gives a parent run per compile and a child run per trial, but it gets there by
   patching DSPy, not through a callback — so that behaviour is MLflow's to keep working,
   not a contract we can lean on. `mlflow` is already an optional dependency here.

### The async seam — two crossings, two answers

Our loop is async and `Teleprompter.compile` is sync, so the boundary is crossed twice, in
opposite directions, and the two crossings share nothing.

**Their program inside our loop** needs no bridge of ours — it needs the right one of theirs.
`in_process_run` is already `async def (query, payload) -> dict` (`connectors/promptpotter.py` is
the reference impl), so a module declaring `aforward` is simply awaited through `acall`. One
declaring only `forward` goes through **`dspy.asyncify`**, which offloads via `anyio.to_thread`
*and carries `thread_local_overrides` across*. `asyncio.to_thread` is the obvious substitute and
the wrong one: `dspy.settings` is thread-local, so the worker would run under a default
configuration and every measurement would be attributed to a model the user never chose — a
wrong number with nothing to raise.

**Our loop inside their caller** has to drive a loop from a sync method. `asyncio.run` raises
inside a running one, and DSPy's own docs single out Jupyter / Colab / Databricks as a separate
code pattern — so for this audience the running-loop case is the common one, not the edge. Two
entry points, not a shim:

- **`acompile()`** — the async peer, awaited directly by a host that already has a loop. Nothing
  is offloaded and the engine behaves exactly as it does under the CLI.
- **`compile()`** — `asyncio.run` when no loop is running, else the coroutine runs in a dedicated
  thread with its own loop. `nest_asyncio` is not an option: it monkeypatches the loop, and it
  would be a dependency added to *hide* a boundary rather than cross it (root § STOP).

The thread's one cost is nameable. This package installs no signal handlers — its only
`asyncio.run` is `cli/campaign_runner.py` — so nothing breaks by running off the main thread
except that SIGINT never reaches it, and **Ctrl+C stops pausing the campaign**. Every other route
to the same stop is untouched, because they poll `.runtime/pause.flag` rather than catch an
interrupt: the `pause` verb and the webapp control both still work. `compile()` says so in its
docstring and does not try to win the interrupt back.

Verified by reading DSPy 3.1.3's source, and worth leaning on rather than rebuilding:
`dspy.configure_cache()` (completion-level replay, complements our measurement cache —
theirs stores the raw response, ours the scored outcome), `Evaluate(...).results` yielding
`(example, prediction, score)` — the per-sample hook PoBB needs, and the place we win
outright since DSPy has no statistical pruning at all — and `dspy.Parallel` /
`Evaluate(num_threads=)`.

Two of theirs to route around rather than lean on. **`program.save()` is not a safe handoff
format**: `Signature.dump_state` writes fields positionally with no names or types, and
`load_state` zips them back with `strict=False`, so a signature that gained a field reloads
a scrambled prompt with no error. The adapter applies a winner to a live program via
`with_instructions()` and never emits DSPy state. And **a cache hit is not recorded at all** —
`base_lm.py` guards `add_usage` on `response.cache_hit`, so a replayed completion is absent from
`get_lm_usage()` rather than reported as zero (the earlier reading of this as "zeroes
`response.usage`" was wrong). Our measurement cache sits above DSPy's and replays archived tokens
through `_emit_cached_step_tokens`, so the only under-count left is a sample ours missed and
DSPy's served; the usage page tells the caller how to turn DSPy's cache off for exact metering.

## Open

- **Upstream.** DSPy scans no entry-point group for optimizers — verified absent from its
  `pyproject.toml`, not a docs gap. Adoption means a PR into `dspy/teleprompt/`, which
  upstream gates on a benchmark against MIPROv2 / GEPA. We have that harness
  ([`../research/bbeh-comparison/`](../research/bbeh-comparison/)).
- **Phase C item 4 — the observability bridge — is the one part not built.** Nothing hangs a
  DSPy `BaseCallback` onto the loop yet, so a compile emits our own telemetry and none of theirs.
- **Never proven end to end against a live provider.** The seams are each exercised — the
  materialized dataset parses, the metric's score reaches `pipeline_data`, the caller's LM class
  survives a tuned copy, `compile()` inside a running loop takes the thread path — but no
  campaign has been run through `acompile` on a real model. That is the first thing to do with it.
