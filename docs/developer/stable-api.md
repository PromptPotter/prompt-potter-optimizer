# Stable API surface — what forks can rely on

> **Stable API v1**

What downstream forks build on. Anything not listed is **internal** — free to rename or delete in any PR. Breaking changes here bump the major; pre-release the version is informational only.

## 1. Connector protocol

The frozen dataclass `promptpotter/connectors/protocol.py::Connector`: **the dataclass is the roster and its field docstrings are the contract.** Six fields are required (`name`, `wire_adapter`, `cell_envelope_s`, and the money each cell draws: `cell_payer`, `sent_spend_bound`, `cells_priced`), so core infers no payer or price; every other one defaults, and `connectors/__init__.py::_validate` raises on a combination that cannot run. Four declarations whose omission yields WRONG NUMBERS rather than a missing feature: `required_observation_keys`, `identity_config` (what every cell is measured WITH when the wire payload does not say), `sent_spend_bound` (an unbounded cell cannot run under a ceiling) and the answer shape — owned by [`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md). `SessionProtocol` is `promptpotter/domain/connector.py`'s. `in_process_run` is handed the measured `Sample` and an `InProcessWorkload` carrying `experiment` and `program` (§5b); per-run state rides that workload ([`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § Execution mode).

**Registering one from your own package — no fork.** `promptpotter.connectors` is a published entry-point group:

```toml
[project.entry-points."promptpotter.connectors"]
anything = "my_package.connector:CONNECTOR"
```

The object must be a `Connector`; **its `name` field is the registry key**, so a package cannot claim a key its connector does not declare. Reference impls: [`connectors/termnorm.py`](../../promptpotter/connectors/termnorm.py), [`application/runner/inner/spawn.py`](../../promptpotter/application/runner/inner/spawn.py). A plugin is trusted code, not sandboxed ([`connectors/CLAUDE.md`](../../promptpotter/connectors/CLAUDE.md) § A connector is trusted code, not sandboxed); v1 promises entry points never become one. Built-ins are not entry points. Beyond `protocol.py`: wire adapters MUST be pure `(sample, PipelineParams) → dict` over the typed carrier (`promptpotter/domain/pipeline_params.py::PipelineParams`), the `identity_config` layer removed before the call · `extract_experiment` MUST return the panel's rows in panel order.

---

## 2. Scoring formula DSL

Configured per dataset via `{dataset}/dataset.yaml::campaign.scoring`:

```jsonc
{
  "scoring": {
    "per_sample": "acc",                    // required: was this cell RIGHT
    "per_cell": "acc * 500 / max(1, latency)" // optional: what it was WORTH — θ is fit on this
  }
}
```

**The scorer id is derived, never declared** — `compiler.py::auto_scorer_id` over everything that grades a cell; a run stamps the one it graded under at `index.json::final.scorer_id`.

Addressable namespace (`application/scoring/formula/compiler.py`): **builtins** — `SAFE_BUILTINS`, arithmetic and `math` only · **terms** — every per-sample evaluator in `_REGISTRY` (`application/scoring/evaluators.py`), banked into the row, and in a `per_cell` formula the core terms `domain/cell_terms.py::CELL_TERMS` names (`evaluators.py::CELL_TERM_META` is their served projection) plus the connector's own `Connector.cell_terms`, served on the campaign's pipeline answer and read off the row's `PipelineData.cell_report`; a campaign's own `judges` (§3) by their term keys, deliberately not in that registry · **matchers** — `MATCHERS` (`application/scoring/formula/matchers.py`), each one's `score` in the same namespace: the LABEL arm that reads answer prose and decides HIT/MISS. Names are stable, implementations may change — read the registry. Calls outside it are rejected at compile time.

One stable signal every measurement carries: **`fitness`** — continuous, `[0,1]`, written only by `Scorer.grade`; a surface needing the discrete word derives it (`domain/scoring.py::is_hit`).

---

## 3. Dataset config schema

The layout (`dataset.yaml` with `source:` / `pipeline:` / `campaign:` / `panel:`, `task.md`, `origin.yaml`, `README.md`) is owned by [`promptpotter/assets/datasets/CLAUDE.md`](../../promptpotter/assets/datasets/CLAUDE.md). What v1 promises:

- **`pipeline`** is the wire shape of [`node-standard.md`](node-standard.md) plus an operator overlay; `available_models` is the check-in's model MENU and the fallback bound on `model` for a node declaring no `optimizer.param_allowed_values.model` — that per-node list is the PERMITTED set.
- **`campaign`** is `domain/campaign_config.py::CampaignConfig` (`extra="forbid"`). Its `optimization` knobs are `Annotated[T, Knob(*estimands)]` fields — the bench's on `OptimizationConfig`, an optimizer's on its member's knob model — walked by `application/knobs.py`, which refuses at import a leaf carrying no `Knob`; read defaults off the fields and the manifest. <!-- probe: git grep -cE "def __init__\(self, \*estimands: Estimand\) -> None" -- promptpotter/domain/campaign_config.py == 1 --> <!-- probe: git grep -cE "carries no Knob\(\.\.\.\)" -- promptpotter/application/knobs.py == 1 --> An optimizer node's provider / model / temperature / `reasoning_effort` / `max_tokens` and each field of its prompt move through `optimization.nodes.{node}.config`.
- **`judges`** maps a scoring term to a registered LLM-as-judge (`{term: {name, stages: [{role, model, provider, temperature}]}}`), banked as a per-sample observation the formula reads by term KEY; its models are inherited from nothing. Third-party judges ride the `promptpotter.judges` entry-point group — [`judges/CLAUDE.md`](../../promptpotter/application/scoring/judges/CLAUDE.md).
- **`origin.yaml`** fields must be ones the node declares (`application/datasets/prompts.py::declared_origin_prompts`); **`task.md`** front matter is the committed framing (`promptpotter/application/bench/task_context.py::commit_task_framing`).

---

## 4. DispatchHub injection keys

The contract is the mechanism, not the slot list: `dispatch/injections/registry.py::injection_table()`, per-slot detail in [`dispatch-hub.md`](dispatch-hub.md) § Reference.

## 4b. Roots — where the package reads and writes

Four roots, owned by `promptpotter/config/paths.py`; the resolution rules are stable, the constants internal.

| Root | Resolves to | Contents |
|---|---|---|
| **Install content** | `promptpotter/assets/` inside the package | optimizer manifests + `resolved_schemas.json` + `sets/{name}.yaml`, the exported dashboard; ships in the wheel |
| **User data** | `$PROMPTPOTTER_HOME` → the checkout's `.promptpotter/` from a source tree → the OS app-data dir | campaigns, sessions, measurements, jobs, identity |
| **Configuration** | `$PROMPTPOTTER_ENV_FILE` → the checkout's `.env` from a source tree → the user-data root's `.env` | keys and postures (`PROMPTPOTTER_AUTH`); a process variable outranks the file |
| **Templates** | `promptpotter/assets/datasets/` (`config/paths.py::install_datasets_root`) | read-only dataset definitions; anything DERIVED lands in the user-data root ([`infrastructure/store/CLAUDE.md`](../../promptpotter/infrastructure/store/CLAUDE.md) § Dataset content has three tiers); a checkout's `research/datasets/` (`config/paths.py::research_datasets_root`; a wheel has none) resolves after both and is never listed; a name reads from the first tier holding it |

`PROMPTPOTTER_HOME` and `PROMPTPOTTER_ENV_FILE` are read once at import. `$PROMPTPOTTER_HOME/optimizers/{name}/pipeline.yaml` and `$PROMPTPOTTER_HOME/checkin/pipeline.yaml` are stable: present, one replaces the packaged manifest of that name; the generated `resolved_schemas.json` beside each is not overridable. `$PROMPTPOTTER_HOME/block_packs/{name}/pack.json` is stable (`config/paths.py::block_packs_root`): a pack the `block_pool` node's `packs` knob may name, shaped `domain/prompt_block.py::BlockPack`, read-only to every account; an account's own sit at `projects/{tenant_id}/block_packs/{name}/pack.json` (`config/prompt_blocks.py::PackShelf`), and one name held by both is refused. `scripts/build_release.py` is the supported way to build a wheel — a bare `uv build` serves no dashboard. Nothing resolves to `site-packages/`.

## 5. CLI flags — `new` and `resume`

The loop-mint verbs. The flag set is `presentation/cli/commands/verbs.py` and what each does to the tree is [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md)'s; v1 promises the two verbs and their declared flags keep their meanings. Not readable off `--help`: every `new` mints a fresh `campaign_id`, but two `new` calls on an unchanged declaration SHARE their content-addressed root `cycle_id` and its origin score, then diverge from round 1 (`runner/mint.py::root_cycle_id_of`); a launch flag SETS an axis of the cycle's budget over what the dataset declares and stays on the standing budget for later resumes (`set-limits` moves it mid-flight). Maintenance and diagnostic verbs are not part of v1.

## 5b. Embedded launch entry (Python)

Every name `application/embedded_run.py`'s `__all__` exports (`run_head_to_head` mints a head-to-head's arms in one act) — signatures there; every other verb (arms, `pause`, `cancel`, a bench grade, a compaction) is a command the host files as every door does: `host_dispatcher(stores).dispatch(CommandCall(payload))`, or `.answer(handler, call)` to read its typed result. Every name lands the data root and sweeps what a dead producer left first (`enter`), so a host that writes under the root (a definition) calls `enter` before it. What a host cannot read off a signature:

- **One call opens its own session**: on a dataset name it mints through the same `mint_framed_cycle` prologue `new` and the web mint run, so the cycle is resumable, forkable and diagnosable by the §5 verbs; on a `CycleHop` it resumes that cycle. The session it answers is bound to the cycle the run ended on, a fork included.
- **`campaign_config` is what a MINT declares** (the dataset's own where none), refused on a resume.
- **A run measures its bank and nothing else**; `rows` hands the host's samples in as it on a mint. `program` rides the backend client as `InProcessWorkload.program` (§1).
- **`budget`** is `domain/spend.py::Budget`, admitted through the one admission every launch takes; `mode` is `domain/run_mode.py::RunMode`.
- **`origin_gate` defaults to `strict`**: `run_campaign` ends `origin_gate` on an unhealthy round 0, before the bench spends; resumed still unhealthy, it passes. Under `arm_gate` `review` / `author` every round blocks until a `submit-arms` command answers.
- The readout lands in the cycle's `readout.log` whatever the host passes; `readout_sink=print` shows it as written.

Nothing on this path imports a server: `pip install promptpotter` is the engine, `[api]` adds the API and the dashboard. `ConfigLayers(dataset_campaign_block(dataset), {"optimization": {…}}).config()` (`application/datasets/authored.py`) shapes a dataset's `campaign` for one launch: a sparse `optimization` mapping merged before validation, so an unknown knob raises — the merge `pipeline_resolve.py::laid_config` makes for a web mint, a check-in and the terminal.

## 5c. The export artifact

`cycles/{id}/export.json` answers *what the run found, and how good it is*, for a reader that never opens the campaign tree; written by `runner/output.py::RunArtifacts` as the run drains, off the ledger that stamps `index.json::final`, so both are projections of one ledger.

```python
from promptpotter.domain.export import parse_prompt_export
export = parse_prompt_export(Path("…/export.json").read_text())
fields = export.prompts["solve"]      # per prompt node, its fields by name, as scored
prompt = export.render("solve")       # that node's prompt as scored: those fields, then its shots
export.measurement.own.composite      # under export.measurement.formula, never a bare number
```

Four rules hold it (`domain/export.py` argues them): **fields by name** · **provenance inside the file** — the fitness under its named formula as the winner's `own` level with its n, the paired lift + CI over the parent (`vs_reference`) and the origin (`vs_origin`), the held-out lift as `bench.vs_origin`, θ, the rows' hash, the optimizer manifest · **an `artifact_version` a reader refuses on** · **JSON and scalars, never pickle**. `tuned_params` carries the winner's node config minus each rendered prompt. `lineage` is one entity per candidate — the origin whole, every other only its `changes` over its parent — joined by PROV-O's `wasDerivedFrom` / `wasGeneratedBy`, JSON-LD through its `@context`. Write it with `export.to_json()`; the `export` verb and route serve it, and its `package` tier wraps it as an Agent Skill. The node config names the model the winner was won on, and a reader treats it as binding: a model swap is a new run ([`../research/external-constraints.md`](../research/external-constraints.md) § Direction — where optimization pays, and where it does not). Absent when no round ever closed.

## 6. Ledger event types

Typed records on the per-cycle ledger (`.runtime/ledger.jsonl`; the workspace-scoped `.workspace/events.jsonl` carries workspace lifecycle, not cycle records). The record family is the discriminated union in `domain/run_records.py`; each record's fields are its dataclass.

Forks within a family share one event stream through `ledger.py::ledger_chain`: the forked cycle's FILE holds only its own appends, the chain spans the parent's records up to `offset` in front of them (a `LedgerSpan` each), and the parent carries a `ResumeCheckpointRecord` of kind `FORK_CUT`; the cut address is `index.json::forked_at_offset`. A cut is `(cycle, offset)` (`domain/cycle_paths.py::Cut`): `offset` is the 0-based line in that cycle's OWN ledger — the one space `ProjectionEnvelope.sequence`, `StepFacts.offset` and every artifact's `at_offset` share — and the inherited prefix rides its `LedgerSpan`'s `until`, outside it. A cut names a `CycleHop`, not a `CyclePath` (the WIRE address). Every artifact stamps the cut it is of, so the ledger is the truth and each file a cache: `?at=<offset>` on the dashboard route re-folds any past moment (`read_model.py::Moment`). Address a record as `(path, offset)`, never by an integer alone. Projections are fed by their ledger's `LedgerIndex` via `Projection.on_record` and write no campaign artifact beyond their declared allowlist.

### The event stream

`GET /api/v1/campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe` (`text/event-stream`) tails that ledger; the frame shape is [`events-asyncapi.generated.json`](../specs/events-asyncapi.generated.json) — `domain/projection_envelope.py::ProjectionKind` covers the whole `CycleRecord` union plus the synthesized `stream_snapshot`, asserted at import. Code path: `infrastructure/projections/event_stream.py::CycleLedgerTail` → `application/cycle_reads.py::cycle_event_frames` → `routers/reads.py::_events`. The contract, permanent ([`decisions.md`](../decisions.md) § 0001):

1. **Snapshot first**: a `stream_snapshot` whose `payload` is the cycle's served dashboard (`"warming_up": true` before a launch declared what the cycle runs with) and whose `sequence` is the offset the tail picks up at.
2. **Live tail**: every later record as one envelope, `sequence` = its ledger offset, dense and in append order; a jump from N to N+2 is a gap, and the recovery is to re-subscribe. Density is why `kind` must cover every `record_type`: an unmapped kind reads as a gap and drives a reconnect.
3. **Heartbeat**: an SSE comment every 15 s; clients key on its arrival, never its text. A finished cycle serves its snapshot, then idles on heartbeats.
4. **No replay** — no `since=`, no `Last-Event-ID`: history is the step window, `GET /hierarchy/{course}/@`, one merged chronology across a course, its forks and inner runs; a live frame's `sequence` IS a step's `facts.offset`.

Every frame carries `activity` whole — `status`, `notices`, and `decision` with each answer as the command to send — folded by the tail from the ledger (`domain/activity.py::ActivityFeed`, each `ActivityKind`'s `LIFETIME`), never by a client, so a client keeping only the newest frame's `activity` is current. A client assumes no mutation succeeded before its door answered it; a loop command's take shows in the run state it moves. The webapp's implementation is `webapp/lib/hooks/useCycleEvents.ts`. A new kind: `ProjectionKind`, the record on `CycleRecord` (or `_PROJECTION_ONLY`), then its arm in `ActivityRecord` and case in `ActivityFeed._item` — a skipped step raises at import, and `gate.py --only specs` refuses a stale spec. Any process sharing the filesystem streams any run.

## 7. Per-cycle artifact paths

What each file holds is [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) § File reference. v1 promises only: inside `campaigns/{campaign_id}/cycles/{cycle_id}/`, **`rounds/round_NNNN.json` + `index.json` + `log.md`**, and `export.json` (§5c). Everything under `.runtime/` may change shape between minor versions — §6 promises the record family, not the file layout. Sibling cycles (forks, diag) live flat under `cycles/`, each with its own `dashboard.json`, seeded from the parent at the cut.

## 8. What is NOT stable

- **Internal module structure, private names, and runtime dataclass shapes** beyond §1–§7; package `__init__` files re-export nothing — this document is the public surface. In-memory caches. Prompt templates at `promptpotter/assets/optimizers/potter/pipeline.yaml::resolved_prompts` — data, tunable on any release. The optimizer node types (`application/optimizers/nodes.py`) and the `NodeContext` a member is handed. Test helpers and the `webapp/` layout.
- **The REST API** (`docs/specs/openapi.generated.json`), the stream's §6 contract aside. Identity is a browser SESSION COOKIE (`promptpotter_session`, `presentation/api/middleware/oidc.py`); no route takes an API key or an inbound bearer token. A third party reaches the campaign surface only with `PROMPTPOTTER_AUTH=off` — not an integration surface.
