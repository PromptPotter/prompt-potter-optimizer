# datasets/ — per-dataset configuration

Each subdirectory is a **first-class dataset definition**: the campaign config, pipeline overlay, optimizer prompts, and human-facing description for one optimization target. Configs are the **source of truth** — no parallel default ladders elsewhere in code.

> Architecture entry point: [`../docs/architecture.md`](../docs/architecture.md) §0 + §0.5.
> Per-dataset prompt store + overlay merge contract: [`../promptpotter/application/CLAUDE.md#backend-overlay`](../promptpotter/application/CLAUDE.md).

## Canonical layout

```
datasets/{name}/
├── campaign.json          # CampaignConfig: optimizer LLM, scoring, max_rounds, etc.
├── pipeline.json          # Backend tunable overlay (nodes.{name}.config)
├── task_description.md    # L1's framing input — what the task IS
├── dataset.md             # Human-facing description: source, split, sample shape
├── prompts/{node}.json    # Per-node PromptTemplate overrides (optional)
└── cache.json             # Origin score cache (write-managed; don't hand-edit)
```

Optional:

- `recon_variants.json` — pre-computed L1 sweep variants for recon runs.
- `sweep/` — sweep-mode sibling cycle outputs.

## Sole route for backend tunable changes

**Backend overlay (`nodes.{name}.config` in `pipeline.json`) is the only way to switch model, provider, temperature, or anything in a node's `optimizer.param_keys`.** Never edit the backend repo (including the co-owned TermNorm backend) to achieve a tunable switch. Pipeline-agnostic is a §0 commitment.

`load_dataset_node_overlay` → `configure_and_apply_pipeline()` (`promptpotter/application/config.py`) merges the overlay onto each wire payload. **The dataset owns its task model** in `nodes.{node}.config.model` — every LLM node must declare one, or `configure_and_apply_pipeline` raises a loud setup error (no silent fall-through to the backend's own default).

## Registered datasets

The roster is the directory listing; each dataset's connector is read off its own `pipeline.json::nodes` — don't mirror either here. The special cases worth knowing:

- **`lca-termnorm`** (`termnorm`) — the multi-node retrieval pipeline. Every other benchmark declares a single `llm_only` **node**, but that is a node name, not the `llm_only` *connector*: all of them are `backend_type: "termnorm"` and route over HTTP to the server exactly as `lca-termnorm` does. No committed dataset runs in-process.
- **`aime_2025`** — its overlay routes to OpenRouter+Mistral, off the Groq default.
- **`email-tagging`** — the built-in try-and-learn demo, surfaced while `User.demo_mode_enabled`.
- **`justlogic`** — the L4 inner benchmark; **`promptpotter-self`** (`promptpotter` connector) — the one L4 dataset ([§ L4 below](#l4--promptpotter-self)).
- **`_optimizer/` + `_optimizer_meta/`** — not datasets: the optimizer's own prompt homes. Which prompts live where → [`../docs/glossary.md`](../docs/glossary.md) **Prompt homes**. Both are **operator-owned files** — nothing writes them. `meta_champion/` ranks meta-prompt states; graduating a winner into `_optimizer/pipeline.json` is a deliberate hand-edit.

## L4 — `promptpotter-self`

`datasets/promptpotter-self/` is the **recursive case**: the outer cycle's mutation surface is the inner cycle's meta-prompt template fields (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan`), exposed via `pipeline.json::nodes.{node}.optimizer.param_keys`.

L4 is **not** a 4th `LayerStrategy` — it is the same PromptPotter applied to itself via the `promptpotter` connector, a recursion, not a new layer driver (full statement: [`../promptpotter/application/optimization/CLAUDE.md`](../promptpotter/application/optimization/CLAUDE.md)).

Status + the remaining work live in ONE place — [`../docs/specs/l4-outer-loop.md`](../docs/specs/l4-outer-loop.md) § Finish line (don't restate it here; it re-goes-stale every slice). Concept doc: [`../docs/concepts/optimizer-of-the-optimizer.md`](../docs/concepts/optimizer-of-the-optimizer.md).

## Reference points — consult on every dataset question

Source of truth for wire / reject rationale, projection-bias findings, per-dataset model defaults:

- **Adding a dataset + canonical splits** → [`../docs/operations/adding-a-dataset.md`](../docs/operations/adding-a-dataset.md). Research the canonical split; never invent one.
- **Why X is / isn't wired, trialed-and-rejected list** → [`../docs/operations/dataset-selection-rationale.md`](../docs/operations/dataset-selection-rationale.md). Check first when asked "why didn't we use Y?" or "have we trialed Z?".
- **Per-dataset model + `reasoning_effort` + `max_tokens`, BBEH output-ceiling traps, Groq daily-volume swap protocol** → [`../docs/operations/dataset-reasoning-matrix.md`](../docs/operations/dataset-reasoning-matrix.md). This — not meta-campaign NOTES.md — is the canonical source for model recommendations.

## Conventions

- **Origin = conservative floor** — the overlay starts at each tunable's floor; contract in [`../promptpotter/application/optimization/CLAUDE.md`](../promptpotter/application/optimization/CLAUDE.md).
- **Don't hand-edit `cache.json`.** It's written by the origin scoring path.
- **`task_description.md` is L1's framing input** — written for the LLM that will generate candidates, not for human readers (though it should be readable).
- **`dataset.md` is operator-facing** — describes source, split, sample shape; cite the canonical evaluation protocol.
