# Prompt Decomposition Scheme

PromptPotter decomposes monolithic prompts into independent fields for perturbation, measurement, and optimization.

## Key Concept: Two Parameter Namespaces

**Prompt scheme fields** live on `OptSearchPoint` / `PromptTemplate` and render into a single prompt string via `render()`. **Pipeline node params** (`pipeline_params`) are a separate namespace — nested dicts keyed by node name (e.g., `{"token_matching": {"thinking_style": "single_pass"}}`). Some names overlap (e.g., `thinking_style` appears in both).

L1 candidates use `pipeline_params_override` for both namespaces: keys matching `PROMPT_STRING_FIELDS` are auto-routed to `derive_candidate()` (updating prompt scheme fields); all other keys stay as node-level pipeline overrides.

---

## Field Registry

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

Source of truth: `PROMPT_STRING_FIELDS` in `promptpotter/shared/constants.py` (fields 1-6). `few_shot_examples` and `plan` are rendered explicitly after the string fields.

Dynamic field mutation (L2-driven add/remove) is a future optimization direction — see [optimization.md § Dynamic Field Set](optimization.md#dynamic-field-set-design-vision).

---

## Rendering Pipeline

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

**Example:** Given `persona = "You are a domain expert."`, `instruction = "Match the query to the best candidate."`, `thinking_style = "Think step by step."`, and all other fields empty, `render()` produces:

```
You are a domain expert.

Match the query to the best candidate.

Think step by step.
```

`compile_prompt()` adds a second pass: substitutes `{{variable}}` placeholders with runtime values (Langfuse-compatible).

---

## Field Usage by Prompt Type

See [overview.md § SearchPoint Hierarchy](overview.md#searchpoint-hierarchy) for the full `SearchPoint` → `PromptTemplate` → `OptSearchPoint` class hierarchy.

In optimizer prompts, `problem_description` carries analytical evidence (eval stats, scan data, critique, escalation). `instruction` carries task directives. `plan` carries L3's strategic framework.

**Generic meta-prompts, task-specific via injection.** Meta-prompt files in `application/optimization/prompts/` are dataset-agnostic. Task-specific details flow through `task_context` injection into `problem_description` and `instruction` template variables — there are no per-task or per-dataset prompt sets, and base prompts must not contain pipeline-specific language. This is a deliberate constraint that keeps the meta-prompt count from multiplying with each new benchmark; adding a dataset is a config change, not a prompt-fork.

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

All optimizer prompts follow: JSON template (`promptpotter/application/optimization/prompts/`) → `load_optimizer_prompt()` → `compile_prompt()` → `llm_call()`.

---

## Optimizer Meta-Prompts

The optimizer's own prompts are themselves `PromptTemplate` instances — the 8-field decomposition applies recursively. Every meta-prompt file under `promptpotter/application/optimization/prompts/` populates the same 6 string fields (`PROMPT_STRING_FIELDS`), plus `plan` where applicable. This is what lets a future outer loop perturb them the same way the core loop perturbs target-backend prompts.

| Template file | Consumer | Compile variables |
|---|---|---|
| `meta_scan_aware.json` | `l1_generate()` — `nodes/generate.py` | `n_variants`, `accuracy_pct`, `n_queries`, `rendered_prompt`, `context_sections` |
| `critique.json` | `CritiqueAgent.run()` — `nodes/critique.py` | `stat_sections` |
| `l2_refine_strategy.json` | `refine_strategy()` — `nodes/layer_transitions.py` | `current_params`, `task_context_section`, `intelligence_sections` |
| `l3_modify_plan.json` | `modify_plan()` — `nodes/layer_transitions.py` | `current_plan`, `l2_summary`, `rendered_prompt`, `pipeline_section`, `intelligence_section` |
| `restructure.json` | `decompose_prompt_fields()` — `pipeline.py` | `consultation_instruction` |

Loader: `load_optimizer_prompt()` at `application/optimization/pipeline.py:218`. The returned `PromptTemplate` is defined at `domain/opt_search_point.py:56` — `prompt_field_dict()` emits the 6 string fields plus `few_shot_examples` for observability tracing.

---

## Projection to Target Pipeline

Three transformations bridge optimizer state to the wire:

| Step | Function | Input → Output |
|------|----------|----------------|
| 1 | `render()` | 6 prompt fields → single string |
| 2 | `to_job_search_point()` | OptSearchPoint → frozen `JobSearchPoint` with prompt in `pipeline_params[prompt_node]["prompt"]` |
| 3 | `run_match()` | `pipeline_params` dict → backend `node_config` wire payload |

`to_job_search_point()` also carries `prompt_fields` (the decomposed dict) on the `JobSearchPoint` for variant derivation without round-tripping through `OptSearchPoint`.

---

## Two prompt stores

PromptPotter keeps two independent prompt stores, each answering a different question.

### 1. Per-dataset starting points — `datasets/{name}/prompts/*.json` (canonical)

**The one true source for the initial `JobSearchPoint`.** Each file is a full `PromptTemplate` JSON — the 6 canonical fields (`persona`, `task_intent`, `problem_description`, `instruction`, `thinking_style`, `answer_format`) plus optional `few_shot_examples` and `plan`. Loaded via `application/datasets/prompt_store.py::load_node_prompt(dataset, node, variant)`. The campaign's `campaign.json` picks a variant name via `starting_prompt` (defaults to `"default"`); `configure_and_apply_pipeline` projects each prompt-bearing node's canonical template into `pipeline_params.<node>.prompt` as an override before the first round.

**Layout:**

```
datasets/{name}/prompts/
  default.json              # single-node datasets (BBEH, GSM8K, AIME, HotPotQA)
  {node_name}.json          # multi-node datasets (TermNorm: entity_profiling.json, llm_ranking.json)
```

**Resolution per node:** `{node_name}.json` wins when present; otherwise `{starting_prompt}.json` (typically `default.json`) is used. Missing both is a hard error at init time — author the canonical template first.

Add alt starting points by dropping more files in the same directory (`zero_shot.json`, `cot_explicit.json`, etc.) and pointing `starting_prompt` at them.

**Deprecated — do not author new instances:** a monolithic `"prompt"` string inside `datasets/{name}/pipeline.json` → `nodes.{node}.config.prompt`, or `"prompt"` as a single atomic axis in `optimizer.param_keys`. Both are emitted as deprecation warnings by `parse_pipeline_response()` at load time. The blob-in-config is ignored whenever a canonical file exists, and the atomic `"prompt"` axis prevents per-field L1 perturbation, variant-library filtering, and critique/L2 field-level diagnostics. Replace with the 6 canonical fields in `param_keys` and move the prompt text into `datasets/{name}/prompts/{node_or_default}.json`.

### 2. L1 crossover / recombination pool — `promptpotter/config/prompt_variants.json`

**Not a starting-point store.** Task-agnostic material that L1 recombines from during optimization, and the seed corpus for recon axis variants. Loaded from `application/intelligence/variant_library.py` and consumed by both the optional sensitivity scan (`application/recon/`, OAT perturbation) and the core optimization loop's L1 generator. Never read at init time as the source of the baseline prompt.

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

`filter_variant_library()` (`adaptive_recon.py`) drops prompt field axes when the pipeline has no LLM node with `prompt_meta`. In practice, this means prompt fields are inactive when the only LLM node (e.g. `llm_ranking`) is excluded from the pipeline.

---

## Potter Trace Dataset

The `potter_traces` dataset loader (`application/datasets/trace_dataset.py`) is the raw material for future self-optimization. It reads archived `campaigns/{cycle_id}/trial_NNNN.json` files and emits one row per round-to-round transition — the potter-state context at round N, the prompt change the potter actually made, and the accuracy delta that resulted. Pure-read, no new persistence, registered through the normal `DATASET_LOADERS` dict.

Row schema:

| Key | Type | Source |
|---|---|---|
| `query` | str | `f"{cycle_id}:round_{N}"` — identifies the transition |
| `ground_truth` | str | `trial[N+1].prompt_fields.changes_description` (or label) |
| `round_context` | dict | Serialized `OptSearchPoint` at round N + critique_text, l2_directive, optimizer_params, prev_accuracy |
| `score_delta` | float | `trial[N+1].accuracy - trial[N].accuracy` |
| `prev_prompt` | str | `OptSearchPoint.render()` at round N |
| `next_prompt` | str | `OptSearchPoint.render()` at round N+1 |
| `escalation_layer` | str | `"L1"` / `"L2"` / `"L3"` inferred from which field changed |

Escalation-layer rule (deterministic, no import from `optimization/`): `plan` changed → L3; else `optimizer_params` changed or `l2_directive` set fresh → L2; else L1.

Usage: `load_dataset("potter_traces", store=..., backend_id=...)`. Natural scoring formula via `compile_scorer()`: `"score_delta"` — feeds directly from the row into the outer potter. No outer scoring loop exists yet; when it does, it will score meta-prompt variants by replaying these rows (see [research/benchmarks.md](../research/benchmarks.md) for the broader self-optimization direction).

---

## Key Files

- `promptpotter/shared/constants.py` — `PROMPT_STRING_FIELDS`
- `promptpotter/domain/opt_search_point.py` — `PromptTemplate` + `OptSearchPoint` (render, derive, project)
- `promptpotter/application/optimization/prompts/` — meta-prompt JSON templates

For the `OptimizationMemory` submodel inventory (cross-round optimizer state — `critique_text`, `escalation_journal`, `validation_failures`, etc.) and the validation-failure mechanism, see [optimization.md](optimization.md) "OptimizationMemory state" and "Validation failures as SearchPoint properties".
