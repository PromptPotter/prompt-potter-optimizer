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

`load_dataset_node_overlay` → `configure_and_apply_pipeline()` (`promptpotter/application/config.py`) merges the overlay onto each wire payload. **The dataset owns its task model** in `nodes.{node}.config.model` — every LLM node must declare one, or `configure_and_apply_pipeline` raises a loud setup error (no silent fall-through to the backend's own default). The sibling `llm_defaults` block is **not authoritative**: it's a display snapshot of the backend's `GET /pipeline` and is never read for resolution (`pipeline_parsing.py` ignores it). Don't reach for it as a control.

## Registered datasets

| Name | Backend connector | Headline use |
|---|---|---|
| `lca-termnorm` | `termnorm` | Production TermNorm benchmark; M11 publication target. |
| `bbeh` | `termnorm` | BBEH (M11 headline benchmark). |
| `gsm8k` | `termnorm` (llm_only mode) | Reasoning baseline; meta-campaign proxy benchmark. |
| `hotpotqa` | `termnorm` | Multi-hop QA benchmark. |
| `aime_2025` | `termnorm` (OpenRouter+Mistral overlay) | AIME competition math; overlay routes off Groq default. |
| `justlogic` | `termnorm` | Logic reasoning at variable depth. |
| `email-tagging` | `termnorm` | Built-in try-and-learn demo (n8n inbox email-classification); surfaced while `User.demo_mode_enabled`. |
| `promptpotter` | `promptpotter` | Outer cycle whose backend is the optimizer itself (L4 recursion). |
| `promptpotter-self` | `promptpotter` | Optimizer-of-the-optimizer demo dataset. See [§ L4 below](#l4--promptpotter-self). |
| `_optimizer/` | n/a | The optimizer's own `pipeline.json` + prompt variants — same shape as a target backend's pipeline.json (per §0 self-optimization commitment). |

## L4 — `promptpotter-self`

`datasets/promptpotter-self/` is the **recursive case**: the outer cycle's mutation surface is the inner cycle's meta-prompt template fields (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan`), exposed via `pipeline.json::nodes.{node}.optimizer.param_keys`.

L4 is **not** a 4th `LayerStrategy` inside `promptpotter/application/optimization/`. It is the same PromptPotter applied to itself via the `promptpotter` connector (`../promptpotter/connectors/promptpotter.py`). Conceptually L2 / L3 / L4 are the same family — each mutates a slower-changing surface of the level below (L2 → `task_context`; L3 → `plan`; L4 → meta-prompt templates) — but structurally L4 is a recursion, not a new layer driver.

**Status:** inner-cycle execution **SHIPPED & live-validated** (l4-outer-loop slice 2) — `new promptpotter-self` runs the real recursion: each outer query (`inner_tasks.json`) mints + runs a sandboxed inner campaign in its own asyncio task (`promptpotter/application/runner/inner_recursion.py`) under a **flat `<workspace>/.inner/<cycle_id>/` registry** (NOT `.runtime/inner` — Windows MAX_PATH), and the 3 proxy metrics flow into the outer scoring formula (`l1_critique.observation_mappings`).

**Finishing L4 (the agent drives this — [`../docs/specs/l4-outer-loop.md`](../docs/specs/l4-outer-loop.md) § Finish line):** the goal is a **distributable** `promptpotter-self`. Two live-run learnings reshape the remaining work: **(1) gsm8k is RETIRED as the inner benchmark** — its origin aces the target (1.0 ≥ 0.80), zero headroom, so every outer candidate scores identically; the inner benchmark must have **origin < target** (chosen: `justlogic` high-depth, ~0.44). **(2) Slice 3 (specialized `_optimizer_meta/` outer prompts emitting per-node `PromptTemplate` edits) is REQUIRED for any signal** — the standard `_optimizer/` loop emits flat prompt edits that don't reach the inner per-node meta-prompts. Then slice 4 (enriched fitness + inner-spend rollup) + a bounded cheap default config.

Concept doc: [`../docs/concepts/optimizer-of-the-optimizer.md`](../docs/concepts/optimizer-of-the-optimizer.md).

## Reference points — consult on every dataset question

Source of truth for wire / reject rationale, projection-bias findings, per-dataset model defaults:

- **Adding a dataset + canonical splits** → [`../docs/operations/adding-a-dataset.md`](../docs/operations/adding-a-dataset.md). Research the canonical split; never invent one.
- **Why X is / isn't wired, trialed-and-rejected list** → [`../docs/operations/dataset-selection-rationale.md`](../docs/operations/dataset-selection-rationale.md). Check first when asked "why didn't we use Y?" or "have we trialed Z?".
- **Per-dataset model + `reasoning_effort` + `max_tokens`, BBEH output-ceiling traps, Groq daily-volume swap protocol** → [`../docs/operations/dataset-reasoning-matrix.md`](../docs/operations/dataset-reasoning-matrix.md). This — not meta-campaign NOTES.md — is the canonical source for model recommendations.

## Conventions

- **Origin = conservative floor.** Overlay starts at the floor of each tunable (`reasoning_effort: "low"`, low temperature, minimal thinking_budget). L1 expands upward from there when sibling-yield or stall evidence supports it. See [`../promptpotter/application/optimization/CLAUDE.md`](../promptpotter/application/optimization/CLAUDE.md).
- **Don't hand-edit `cache.json`.** It's written by the origin scoring path.
- **`task_description.md` is L1's framing input** — written for the LLM that will generate candidates, not for human readers (though it should be readable).
- **`dataset.md` is operator-facing** — describes source, split, sample shape; cite the canonical evaluation protocol.
