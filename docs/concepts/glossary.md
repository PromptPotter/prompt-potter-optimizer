# Glossary

Authoritative definitions of terms used across the documentation. If a doc uses one of these terms in a different sense, that's a bug — flag it.

---

**Active session** — the pointer that tells every command which campaign is current. Lives at `.promptpotter/active_session.json`. Set by `init`; read by everything else. Lets you run `optimize` without passing any flags.

**Backend** — the service PromptPotter sends queries to for scoring. Must expose `/matches` (evaluate a query), `/pipeline` (describe its structure), and `/status` (health). Can be a single LLM call or a multi-step pipeline.

**Baseline** — the score of the starting prompt on the scoring slice, computed at the start of every campaign as phase 0 of `optimize`. Every round's winner must beat this baseline to become the new best.

**Campaign** — one complete optimization run. Fixed dataset, fixed pipeline endpoint, fixed starting prompt. A campaign is a sequence of rounds. Synonymous with *cycle* at the identity level (every campaign has a `cycle_id`).

**Candidate** — one proposed configuration scored during a round. A candidate is a specific combination of prompt fields and pipeline parameters. Candidates compete within a round; one winner advances.

**Critique** — the L1 analysis step that reads raw per-query results from a completed round and writes a structured summary: what worked, what failed, what to try next. Feeds the next round's L1 Generate.

**Cycle** — the identity layer of a campaign. The `cycle_id` hashes the problem configuration and locates the campaign's artifact directory. A campaign and its cycle are the same run seen from two angles.

**Dataset** — the collection of queries and ground-truth answers used to score candidates. Ships in `datasets/{name}/` with its own config, starting prompt, and task description.

**Fork** — mint a new campaign from a divergence point in an existing one. Used when the scoring formula changed mid-campaign and resume would be unsafe. The old campaign stays intact as a record.

**L1 / Layer 1** — the normal optimization layer: generate candidates, evaluate, critique. Fires every round.

**L2 / Layer 2** — engaged when L1 hasn't improved for `l1_patience` rounds. Rewrites the task framing fed to L1. Does not touch pipeline parameters directly.

**L3 / Layer 3** — engaged when L2 also hasn't helped. Rewrites the strategic plan — a high-level framework that changes how L1 approaches the search. Rare.

**Node** — one step of the backend pipeline. Can be an LLM call, a retrieval step, a cache lookup, a ranking step, anything. PromptPotter discovers nodes from `GET /pipeline` and optimizes their exposed parameters.

**Patience** — the consecutive-no-improvement counter per layer. When L1's patience hits `l1_patience`, L2 fires. When L2's patience hits `l2_patience`, L3 fires. Resets on improvement.

**Pipeline** — the multi-step computation the backend runs for each query. Can be a single node (one LLM call) or many nodes chained together.

**Pipeline parameters** — nested dicts keyed by node name. Everything in a candidate other than prompt fields. Example: `{"web_search": {"max_sites": 5}, "token_matching": {"threshold": 0.8}}`.

**Prompt fields** — the eight named parts a prompt decomposes into: persona, task intent, problem description, instruction, thinking style, answer format, few-shot examples, plan. Some or all may be exposed by a given pipeline node.

**Rewind** — restart an active campaign from an earlier round, discarding later trials. Same `cycle_id`, archived history. Run via `optimize --from N`.

**Round** — one generate-evaluate-critique cycle inside a campaign. Each round produces a candidate list, scores them all, picks a winner, writes a critique.

**Scorer** — the function that converts a pipeline output into a numeric score against a ground-truth answer. Per-dataset; configured in `campaign.json::scoring`.

**Scoring slice** — the subset of the dataset each round uses for candidate comparison. Controlled by `sp_budget_ttest`. The baseline uses the full slice; individual candidates may be early-stopped.

**Search memory** — the materialized intelligence view that accumulates across campaigns. Tracks parameter impact, query patterns, and failure modes. Feeds a digest into every L1, L2, and L3 round.

**Session** — the operator workspace. A session can host multiple campaigns (1:N today used as 1:1). Session-level artifacts (journal, notes, control surface) live in `sessions/{session_id}/`.

**Trial** — the per-round serialized snapshot of optimizer state. Resume reads from the latest trial. `trials/trial_NNNN.json` is the resume source of truth.

**Winner** — the best-scoring candidate in a round, provided it beats the current best by at least the improvement threshold. A round can have no winner if no candidate beats the bar.
