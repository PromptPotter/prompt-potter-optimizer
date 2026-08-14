# Spec — `promptpotteropt`, the DSPy adapter

**Status: planned, nothing built.** The user-facing page this produces is
[`../developer/dspy-optimizer.md`](../developer/dspy-optimizer.md).

## The split, and why it is at the top

A **fourth distribution tier**. Tiers 1–3 are all *someone runs PromptPotter* — hosted,
local `/potter-run`, self-hosted team. This one is *PromptPotter runs inside someone else's
program*: a DSPy user who will never clone this repo or open the webapp. So it ships as a
**separate repo and PyPI name**, not a mode or a flag here.

**The dependency arrow points one way: `promptpotteropt` depends on
`promptpotter-optimizer`; this package never imports `dspy`.** That is what makes "no new
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
2. **Re-tier core dependencies.** `fastapi`, `starlette`, `python-multipart`,
   `scalar-fastapi`, `uvicorn`, `sse-starlette`, `cryptography` (OIDC) and `openpyxl`
   (xlsx ingest) sit in `[project] dependencies`, so a library install pulls a web server
   and a JWS verifier. Move them to extras; core keeps `pydantic`, `pydantic-settings`,
   `openai`, `httpx`, `python-dotenv`, `filelock`, `numpy`, `json-repair`, `pyyaml`.
   Operators keep installing `.[all,dev]` and notice nothing.
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
4. **The export artifact** — one self-describing JSON, provenance carried inside it, written at
   the seam that already writes `index.json::final`. Shape, rules and the three consumers are
   owned by [`roadmap.md`](roadmap.md) § Application radius; this arc's stake is that it
   **replaces** the adapter's handoff question entirely — the adapter reads an artifact instead
   of re-deriving a winner, and `PromptTemplate(**winner_prompt_fields).render()` (what the BBEH
   harness hand-rolls today) becomes the loader's business, not each caller's.
5. **Consolidate the two telemetry fan-outs.** A fact that must reach both the ledger and
   Langfuse costs eight synchronized edits, not the four `adding-a-surface.md` §1 documents
   ([`code-debt-cleanup.md`](code-debt-cleanup.md) § Ready). Landing this before C4 is what makes
   the observability bridge a bridge rather than a ninth edit.

## Phase C — the adapter, in its own repo

Small by construction, if B lands. Four parts, none of them a loop:

1. **A `dspy` connector** — `execution: "in_process"`, `in_process_run` calls the user's
   `dspy.Module`, registered through the published entry-point group. Reference impls are
   `connectors/promptpotter.py` (in-process) and `connectors/termnorm.py` (wire).
2. **`PromptPotterOpt(Teleprompter)`** — obey `compile(student, *, trainset, valset)`,
   call the Phase-B launch entry, apply the winner back with `with_instructions()`.
3. **`Loop` / `Node`** — the two dataclasses on the usage page, mapped onto
   `OptimizationConfig` and the node overlay through B3.
4. **The observability bridge.** Narrower than first written. `BaseCallback` stops at
   `on_module_*` / `on_lm_*` / `on_adapter_{format,parse}_*` / `on_tool_*` / `on_evaluate_*`
   — **there is no compile-level hook**; `on_compile` appears nowhere in 3.1.3. So round and
   campaign lifecycle emission is ours, and `on_evaluate_start` / `on_evaluate_end` is the
   coarsest thing DSPy offers to hang a round on. `mlflow.dspy.autolog(log_compiles=True)`
   still gives a parent run per compile and a child run per trial, but it gets there by
   patching DSPy, not through a callback — so that behaviour is MLflow's to keep working,
   not a contract we can lean on. `mlflow` is already an optional dependency here.

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
`with_instructions()` and never emits DSPy state. And **a cache hit zeroes `response.usage`**,
so their token accounting reports a replayed run as free — our `TokenUsageRecord.cached`
split must survive the boundary intact.

## Open

- **The async seam.** Our engine is async; DSPy's concurrency is thread-backed
  (`asyncify` bridges one direction). Needs a design pass before C2.
- **Upstream.** DSPy scans no entry-point group for optimizers — verified absent from its
  `pyproject.toml`, not a docs gap. Adoption means a PR into `dspy/teleprompt/`, which
  upstream gates on a benchmark against MIPROv2 / GEPA. We have that harness
  ([`../research/bbeh-comparison/`](../research/bbeh-comparison/)).
- **Phase A2 — dependency re-tiering** is the one item not started: it moves `fastapi` /
  `uvicorn` / `cryptography` / `openpyxl` and their peers into extras, and `pyproject.toml` is
  held by a concurrent PyPI-rename commit. Nothing else in A or B waits on it.
