# Prompt Scheme Internals

PromptPotter decomposes monolithic prompts into independent fields for perturbation, measurement, and optimization. Conceptual overview in [../concepts/prompts-and-individuals.md](../concepts/prompts-and-individuals.md); this page covers the implementation.

The same decomposition applies recursively: the optimizer's own meta-prompts — the templates driving L1, L2, L3, and the critique step — are themselves `PromptTemplate` instances with the same 8 fields. A future outer-loop PromptPotter can optimize the optimizer's prompts using the same machinery.

## Rendering pipeline

`PromptTemplate.render()` joins three groups with blank lines:

```
┌─ PROMPT_STRING_FIELDS (6) ───────────────┐
│  1. persona                              │
│  2. task_intent                          │
│  3. problem_description                  │
│  4. instruction                          │
│  5. thinking_style                       │
│  6. answer_format                        │
│                                          │
│  +/- [???]                               │
└──────────────────────────────────────────┘
┌─ few_shot_examples ──────────────────────┐
│  7. Input/Output pairs (rendered inline) │
└──────────────────────────────────────────┘
┌─ plan ───────────────────────────────────┐
│  8. L3 strategic framework (appended)    │
└──────────────────────────────────────────┘
```

L2-driven add/remove of fields is a future direction — see [../concepts/three-layer-loop.md § The dynamic field set](../concepts/three-layer-loop.md).

## SearchPoint hierarchy

```
SearchPoint (abstract)
├── JobSearchPoint      — target pipeline configuration (frozen)
└── PromptTemplate      — 8-field prompt scheme (render/compile)
        └── OptSearchPoint  — optimizer working state (+ lineage, L2/L3, memory)
```

**Prompt alias groups** link the original monolithic prompt to its decomposed form so historical evaluations stay discoverable across both. Per-query results from prior `dataset_runs/` are reused when a new `SearchPoint`'s `node_configs` share a prefix with a stored run — exact matches reuse everything, partial matches reuse queries that short-circuited before the diverging node.

## Two parameter namespaces

**Prompt scheme fields** — the 8 fields below — render into a single prompt string. **Pipeline node parameters** are a separate namespace: nested dicts keyed by node name (e.g., `{"token_matching": {"thinking_style": "single_pass"}}`). Some names overlap (e.g., `thinking_style` appears in both namespaces).

L1 candidates use `pipeline_params_override` for both namespaces: keys matching `PROMPT_STRING_FIELDS` are auto-routed to `mutate()` (updating prompt scheme fields); all other keys stay as node-level pipeline overrides.

---

## Field registry

| # | Field | Layer | Variants? | Purpose |
|---|-------|-------|-----------|---------|
| 1 | `persona` | L1 | Yes (6) | Who the LLM is — role framing |
| 2 | `task_intent` | L1 | Yes | What the LLM should accomplish |
| 3 | `problem_description` | L1 | Yes | Domain context / situational state / analytical evidence |
| 4 | `instruction` | L1 | No | Core prompt template (primary L1 mutation target) |
| 5 | `thinking_style` | L1 | Yes (35+) | Reasoning strategy guidance |
| 6 | `answer_format` | L1 | Yes | Output structure constraints |
| 7 | `few_shot_examples` | L1 | No | Input/Output demonstration pairs (rendered separately) |
| 8 | `plan` | L3 | No | Strategic optimization framework (rendered at end) |

Source of truth: `PROMPT_STRING_FIELDS` in `promptpotter/shared/constants.py` (fields 1-6). `few_shot_examples` and `plan` are appended by `PromptTemplate.render()` after the string fields.

---

## Two prompt stores

PromptPotter keeps two independent prompt stores, each answering a different question.

### 1. Per-dataset starting points — `datasets/{name}/prompts/*.json` (canonical)

**The one true source for the initial pipeline configuration.** Each file is a full prompt template — the 6 canonical fields plus optional `few_shot_examples` and `plan`. The campaign config picks a variant name via `starting_prompt` (defaults to `"default"`).

**Layout:**

```
datasets/{name}/prompts/
  default.json              # single-node datasets (BBEH, GSM8K, AIME, HotPotQA)
  {node_name}.json          # multi-node datasets (TermNorm: entity_profiling.json, llm_ranking.json)
```

**Resolution per node:** `{node_name}.json` wins when present; otherwise `default.json` is used. Missing both is a hard error at init time — author the canonical template first.

Add alt starting points by dropping more files in the same directory (`zero_shot.json`, `cot_explicit.json`, etc.) and pointing `starting_prompt` at them.

**Deprecated:** a monolithic `"prompt"` string inside `pipeline.json` or as a single atomic parameter axis. Both are flagged with deprecation warnings at load time. Replace with the 6 canonical fields and move the prompt text into `datasets/{name}/prompts/`.

### 2. L1 crossover / recombination pool — `promptpotter/config/prompt_variants.json`

**Not a starting-point store.** Task-agnostic material that L1 recombines from during optimization. Never read at init time as the source of the baseline prompt.

**Index convention (provisional):**
- **Index 0** — empty string (always present; lets the optimizer start from scratch)
- **Index 1** — task-agnostic defaults
- **Index 2+** — dataset-specific and PromptWizard variants from the original library

| Field | Variant count | Source |
|-------|--------------|--------|
| `persona` | 6 | Hand-crafted role framings |
| `task_intent` | ~5 | Task description variations |
| `problem_description` | ~5 | Domain context variations |
| `thinking_style` | 35+ | Research literature (CoT, ToT, etc.) |
| `answer_format` | ~5 | Output structure variations |
| `instruction` | — | Always LLM-generated |
| `few_shot_examples` | — | Not in variant library |

Prompt field axes are dropped automatically when the pipeline has no LLM node with a prompt template. In practice, prompt fields are inactive when the only LLM node is excluded from the pipeline.

---

## Field usage by prompt type

In optimizer prompts, `problem_description` carries analytical evidence (scoring stats, scan data, critique, escalation). `instruction` carries task directives. `plan` carries L3's strategic framework.

**Generic meta-prompts, task-specific via injection.** Meta-prompt templates are dataset-agnostic. Task-specific details flow through `task_context` injection into `problem_description` and `instruction` template variables — there are no per-task or per-dataset prompt sets, and base prompts must not contain pipeline-specific language. Adding a dataset is a config change, not a prompt-fork.

```
┌────────────────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┐
│                │pers. │t.int.│p.desc│instr.│think.│a.fmt │f.shot│ plan │
├────────────────┼──────┼──────┼──────┼──────┼──────┼──────┼──────┼──────┤
│ Job prompt     │  ✓   │  ✓   │  ✓   │  ✓   │  ✓   │  ✓   │  ✓   │      │
│ L1 Generate    │  ✓   │  ✓   │  ✓   │  ✓   │  ✓   │  ✓   │      │  ✓   │
│ L2 Refine      │  ✓   │  ✓   │  ✓   │  ✓   │      │  ✓   │      │      │
│ L3 Plan        │  ✓   │  ✓   │  ✓   │  ✓   │  ✓   │  ✓   │      │  ✓   │
│ Critique       │  ✓   │  ✓   │  ✓   │      │      │  ✓   │      │      │
│ Restructure    │  ✓   │  ✓   │      │  ✓   │      │  ✓   │      │      │
└────────────────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┘
```

Each optimizer prompt template receives a single `inbox` hole holding the assembled intelligence block. L3 keeps additional context holes for anchoring (current plan, L2 history, rendered prompt, pipeline snapshot). See [information-flow.md](information-flow.md) for the per-field table.

---

## Optimizer meta-prompts

The optimizer's own prompts are themselves `PromptTemplate` instances — the 8-field decomposition applies recursively. Every meta-prompt file under `promptpotter/application/optimization/prompts/` populates the same 6 string fields (`PROMPT_STRING_FIELDS`), plus `plan` where applicable. This is what lets a future outer loop perturb them the same way the core loop perturbs target-backend prompts.

Every L1 / L2 / critique / L3 template receives a single `inbox` hole holding the intelligence block assembled by `inbox_registry.assemble_inbox()` (or, for critique, by its own `_assemble_l1_critique_sections`).

| Template file | Consumer | Compile variables |
|---|---|---|
| `meta_scan_aware.json` | `l1_generate()` | `n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt`, `inbox` |
| `l1_critique.json` | `L1CritiqueAgent.run()` | `inbox` |
| `l2_refine_strategy.json` | L2 refine transition | `current_params`, `task_context_section`, `inbox` |
| `l3_modify_plan.json` | L3 plan transition | `current_plan`, `l2_summary`, `rendered_prompt`, `pipeline_section`, `runtime_failures_section`, `inbox` |
| `restructure.json` | `decompose_prompt_fields()` | `consultation_instruction` |

Loader and symbol paths: see [code-map.md](code-map.md).

---

## Projection to target pipeline

Three transformations bridge optimizer state to the wire:

| Step | Function | Input → Output |
|------|----------|----------------|
| 1 | `PromptTemplate.render()` | 6 prompt fields → single string |
| 2 | `OptSearchPoint.to_job_search_point()` | `OptSearchPoint` → frozen `JobSearchPoint` with prompt in `pipeline_params[prompt_node]["prompt"]` |
| 3 | `BackendClient.run_match()` | `pipeline_params` dict → backend `node_config` wire payload |

`to_job_search_point()` also carries `prompt_fields` (the decomposed dict) on the `JobSearchPoint` for variant derivation without round-tripping through `OptSearchPoint`.
