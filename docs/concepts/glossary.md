# Glossary

Term → one-line gloss → page that owns the full definition.

| Term | Gloss | Owner |
|------|-------|-------|
| **Active session** | Pointer at `.promptpotter/active_session.json` telling every command which campaign is current. | [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) |
| **Backend** | The service PromptPotter sends queries to. Must expose `/matches`, `/pipeline`, `/status`. | [`../operations/backend-integration.md`](../operations/backend-integration.md) |
| **Baseline** | Score of the starting prompt on the scoring slice; phase 0 of `optimize`. | [`../operations/cli-reference.md`](../operations/cli-reference.md) |
| **Campaign / Cycle** | One complete optimization run. `cycle_id` hashes pipeline + prompts + dataset. | [`campaign-tree.md`](campaign-tree.md) |
| **Candidate / Individual** | One member of a round's population: prompt fields + pipeline parameters. | [`state-record.md`](state-record.md) |
| **Catalogue** | Code-derived menu of L1-generate sections + scalars rendered into L2's prompt. | [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md) |
| **Critique** | L1's per-round analysis step. Reads raw per-query results; feeds L1-generate next round. | [`the-loop.md`](the-loop.md) |
| **Dataset** | Queries + ground-truth answers, in `datasets/{name}/`. | [`../manual/03-first-campaign.md`](../manual/03-first-campaign.md) |
| **Fork** | New cycle minted from a divergence point in an existing one. | [`campaign-tree.md`](campaign-tree.md) |
| **L1 / L2 / L3** | Generate / Refine / Plan. Three layers of the loop. | [`the-loop.md`](the-loop.md) |
| **L1-generate surface** | Closed catalogue of every variable injected into L1's meta-prompt. | [`../developer/l1-generate-surface.md`](../developer/l1-generate-surface.md) |
| **Measurement archive** | Append-only `library/` of every `(sample × config → outcome)`. | [`scoring-and-memory.md`](scoring-and-memory.md) |
| **Node** | One step of a pipeline. Discovered from `GET /pipeline`. | [`nodes-and-pipelines.md`](nodes-and-pipelines.md) |
| **OSP** | `OptSearchPoint` — the per-round state record. | [`state-record.md`](state-record.md) |
| **OSP mutation** | L2's canonical write onto the record. State that's not on the OSP doesn't survive between rounds. | [`state-record.md`](state-record.md) |
| **Patience** | Consecutive-no-improvement counter per layer. `l1_patience` triggers L2; `l2_patience` triggers L3. | [`the-loop.md`](the-loop.md) |
| **Pipeline** | Multi-step computation the backend runs per query. | [`nodes-and-pipelines.md`](nodes-and-pipelines.md) |
| **Pipeline parameters** | Nested dicts keyed by node name. Everything in a candidate other than prompt fields. | [`state-record.md`](state-record.md) |
| **Probe round** | Round scoped to warned queries only. `action = "probe_round"`. | [`the-loop.md`](the-loop.md) |
| **Prompt fields** | Six prompt-string fields plus two appended sections (few-shot, plan). | [`state-record.md`](state-record.md) |
| **Rewind** | Restart an active campaign from an earlier round. `optimize --from N`. | [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) |
| **Round / Winner** | One generation. Winner is the fittest individual that clears the improvement threshold. | [`the-loop.md`](the-loop.md) |
| **Scorer** | Per-dataset function turning pipeline output into a numeric score. | [`scoring-and-memory.md`](scoring-and-memory.md) |
| **Scoring slice** | Subset of the dataset each round uses for candidate comparison. | [`../methods/candidate-elimination.md`](../methods/candidate-elimination.md) |
| **Search memory** | Cross-campaign axis-keyed digest. Feeds L1, L2, L3. | [`scoring-and-memory.md`](scoring-and-memory.md) |
| **Section override** | L2's write that toggles a section off or replaces its text on the OSP. Persists across rounds. | [`state-record.md`](state-record.md) |
| **Self-healing loops** | Four LLM-to-LLM failure repair loops (Loop 1–4). | [`self-healing.md`](self-healing.md) |
| **Session** | Operator workspace at `sessions/{session_id}/`. Hosts campaigns. | [`../operations/persistence-and-state.md`](../operations/persistence-and-state.md) |
| **Sweep** | Breadth-first comparison of N L1-prompt hypotheses via fork siblings. | [`campaign-tree.md`](campaign-tree.md) |
| **Trial** | Per-round serialized OSP snapshot at `trials/trial_NNNN.json`. | [`state-record.md`](state-record.md) |
