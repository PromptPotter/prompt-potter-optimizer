# Glossary

One canonical name per concept. Code names live in the right column for cross-reference.

| Term (canonical) | Gloss | Code name | Owner page |
|------|-------|-----------|------------|
| **Active session** | Pointer at `.promptpotter/active_session.json` telling every command which campaign is current. | — | [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) |
| **Backend** | The service PromptPotter sends queries to. Must expose `/matches`, `/pipeline`, `/status`. | `BackendClient` | [`../operations/backend-integration.md`](../operations/backend-integration.md) |
| **Baseline** | Fitness of the starting prompt on the scoring set; phase 0 of `optimize`. | `RoundBaseline` | [`../operations/cli-reference.md`](../operations/cli-reference.md) |
| **Campaign** | One complete optimization run as the operator sees it. | filesystem `campaigns/{root_cycle_id}/` | [`campaign-tree.md`](campaign-tree.md) |
| **Candidate** | One member of a round's population. Prompt fields + pipeline parameters. | `OptSearchPoint` (`OSP`) | [`state-record.md`](state-record.md) |
| **Catalogue** | Code-derived menu of signal names (`L1_POSSIBLE`) L2 may put in `l1_layout`; rendered into L2's prompt as the `l1_signal_catalogue` signal. | `L1_POSSIBLE` | [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md) |
| **Critique** | L1's per-round analysis. Reads raw per-query results; feeds L1-generate next round. | `l1_critique`, `l1_critique_text` | [`the-loop.md`](the-loop.md) |
| **Cycle** | Internal id (`cycle_id`) for one optimization run; survives forks via `root_cycle_id`. A campaign is one cycle family. | `cycle_id`, `root_cycle_id`, `CycleLedger`, `CycleRecord` | [`campaign-tree.md`](campaign-tree.md) |
| **Dataset** | The master query list + ground-truth answers in `datasets/{name}/`. | `Session.samples`, `list[Sample]` | [`../manual/03-first-campaign.md`](../manual/03-first-campaign.md) |
| **Fitness** | The numeric output of the scorer. Per-query `fitness: float`; round-aggregate `composite_fitness`. | `QueryMeasurement.fitness`, `composite_fitness` | [`scoring-and-memory.md`](scoring-and-memory.md) |
| **Fork** | New cycle minted from a divergence point in an existing one. Sibling under the same `root_cycle_id`. | `DecisionEvent` kind `FORK_CUT` | [`campaign-tree.md`](campaign-tree.md) |
| **Hit** | Boolean: rank-1 exact match against ground truth. Independent of fitness. | `QueryMeasurement.hit` | [`scoring-and-memory.md`](scoring-and-memory.md) |
| **L1 / L2 / L3** | The three layers of the loop: generate / refine / plan. | `Layer.L1_GENERATE`, `Layer.L2_CONTEXT`, `Layer.L3_PLAN` | [`the-loop.md`](the-loop.md) |
| **L1 layout** | Per-slot list of signal names L2 picks from `L1_POSSIBLE`. Resolved by `DispatchHub.fill_l1` when composing L1's prompt. | `L1Layout`, `OptSearchPoint.l1_layout` | [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md) |
| **Measurement archive** | Append-only `archive/` of every `(sample × config → outcome)`. The cross-cycle DB. | `MeasurementArchive` | [`scoring-and-memory.md`](scoring-and-memory.md) |
| **Mutation** | A change L1 (or L2-via-overrides) makes to the candidate from one round to the next. | `mutate`, `mutation` | [`the-loop.md`](the-loop.md) |
| **Node** | One step of a pipeline. Discovered from `GET /pipeline`. | `PipelineNode` | [`nodes-and-pipelines.md`](nodes-and-pipelines.md) |
| **OSP mutation** | L2's canonical write onto the candidate. State that's not on the OSP doesn't survive between rounds. | — | [`state-record.md`](state-record.md) |
| **Patience** | Consecutive-no-improvement counter per layer. `l1_patience` triggers L2; `l2_patience` triggers L3. | `EscalationState` | [`the-loop.md`](the-loop.md) |
| **Pipeline** | Multi-step computation the backend runs per query. | `PipelineSchema` | [`nodes-and-pipelines.md`](nodes-and-pipelines.md) |
| **Pipeline parameters** | Nested dicts keyed by node name. Everything in a candidate other than prompt fields. | `pipeline_params`, `pipeline_params_override` | [`state-record.md`](state-record.md) |
| **Plan** | L3's strategic write onto the candidate. Persistent — survives `clear_volatile`. Read by both L1 and L2. | `OptSearchPoint.plan` | [`the-loop.md`](the-loop.md) |
| **Probe round** | Round scoped to warned queries only. `action = "probe_round"`. | — | [`the-loop.md`](the-loop.md) |
| **Prompt fields** | Six prompt-string fields plus two appended sections (few-shot, plan). | `PromptTemplate` | [`state-record.md`](state-record.md) |
| **Rewind** | Restart an active campaign from an earlier round in place. `optimize --from N`. | — | [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) |
| **Round** | One iteration of the loop. Generates, scores, picks a winner. | `RoundResult` | [`the-loop.md`](the-loop.md) |
| **Round record** | The per-round file dump under `rounds/`. Captures the candidate's full state at end-of-round so resume + replay work. | filename `round_NNNN.json` | [`state-record.md`](state-record.md) |
| **Scorer** | Per-dataset function turning pipeline output into a numeric fitness. | `Scorer`, `compile_scorer` | [`scoring-and-memory.md`](scoring-and-memory.md) |
| **Scoring set** | The active subset of the dataset used for the current round. | `ScoringContext.scoring_set` | [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md) |
| **Search memory** | Cross-campaign axis-keyed digest. Feeds L1, L2, L3. | `variant_axis_index` | [`scoring-and-memory.md`](scoring-and-memory.md) |
| **Section override** | L2's write that toggles a section off or replaces its text on the candidate. Persists across rounds. | `l1_section_overrides`, `l1_section_overrides_text` | [`state-record.md`](state-record.md) |
| **Self-healing** | Four LLM-to-LLM failure repair loops (Loop 1–4). Recovery from broken outputs. | `heal_l1_validation`, etc. | [`self-healing.md`](self-healing.md) |
| **Session** | Operator workspace at `sessions/{session_id}/`. Hosts campaigns. | `Session` | [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) |
| **Sweep** | Breadth-first comparison of N L1-prompt hypotheses via fork siblings. | — | [`campaign-tree.md`](campaign-tree.md) |
| **Winner** | The fittest candidate of a round that clears the improvement threshold. | `DecisionEvent` kind `ROUND_WINNER` | [`the-loop.md`](the-loop.md) |

## Words we never use loosely

- **`score`** — always qualified: per-query *fitness*, round-level *composite fitness*, *accuracy* (% correct), *validator health*. Bare "score" is forbidden in prose.
- **`config`** / **`params`** — always qualified: `pipeline_params`, `pipeline_params_override`, `optimizer_params`, `node_config` (wire-only).
- **`run`** — operator-facing: prefer **campaign**. The word `run_id` survives only as the content-hash key in `archive/measurements/{run_id}.json`.
- **`state`** — qualified: *escalation state* (the FSM), *campaign bundle* (the per-cycle wiring), or named explicitly.
- **`validation`** — qualified: *schema-compliance check* (L1 output gate), *validator outcome* (mid-round escalation), *schema-violation audit* (persisted record).
- **`evaluator`** vs **`scorer`** — *scorer* is the per-dataset compiled formula. *Evaluator* is the registry abstraction (scorer + critique).
- **`experiment`** — replaced by *campaign* and *cycle*. Don't use.
- **`service`** (in pipeline context) — replaced by *node*. Don't use.
- **`eval`** in identifiers/prose — banned (per CLAUDE.md). Use *evaluation*, *measurement*, *scoring*.
