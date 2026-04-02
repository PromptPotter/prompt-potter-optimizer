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

Source of truth: `PROMPT_STRING_FIELDS` in `api/shared/constants.py` (fields 1-6). `few_shot_examples` and `plan` are rendered explicitly after the string fields.

Dynamic field mutation (L2-driven add/remove) is a future optimization direction — see [optimization.md § Dynamic Field Set](optimization.md#dynamic-field-set-design-vision).

---

## Rendering Pipeline

```
┌─ PROMPT SCHEME ──────────────────────────┐
│  1. persona                              │
│  2. task_intent                          │
│  3. problem_description                  │
│  4. instruction                          │
│  5. thinking_style                       │
│  6. answer_format                        │
│  7. few_shot_examples                    │
│  8. plan                                 │
└──────────────────────────────────────────┘
         │ render()
         ▼
   skip empties → join("\n\n") → prompt string
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

See [architecture.md § Data Models](architecture.md#data-models) for the full `SearchPoint` → `PromptTemplate` → `OptSearchPoint` class hierarchy.

In optimizer prompts, `problem_description` carries analytical evidence (eval stats, scan data, critique, escalation). `instruction` carries task directives. `plan` carries L3's strategic framework.

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

All optimizer prompts follow: JSON template (`api/config/optimizer_prompts/`) → `load_optimizer_prompt()` → `compile_prompt()` → `llm_call()`.

---

## Projection to Target Pipeline

Three transformations bridge optimizer state to the wire:

| Step | Function | Input → Output |
|------|----------|----------------|
| 1 | `render()` | 6 prompt fields → single string |
| 2 | `to_job_search_point()` | OptSearchPoint → frozen `JobSearchPoint` with prompt in `pipeline_params[prompt_node]["prompt"]` |
| 3 | `run_match()` | `pipeline_params` dict → TermNorm `node_config` wire payload |

`to_job_search_point()` also carries `prompt_fields` (the decomposed dict) on the `JobSearchPoint` for variant derivation without round-tripping through `OptSearchPoint`.

---

## Variant Library

`api/config/prompt_variants.json` provides pre-built alternatives per field for the sensitivity scan (OAT).

| Field | Variant count | Source |
|-------|--------------|--------|
| `persona` | 6 | Hand-crafted role framings |
| `task_intent` | ~5 | Task description variations |
| `problem_description` | ~5 | Domain context variations |
| `thinking_style` | 35+ | Research literature (CoT, ToT, etc.) |
| `answer_format` | ~5 | Output structure variations |
| `instruction` | — | Always LLM-generated |
| `few_shot_examples` | — | Not in variant library |

`filter_variant_library()` (`smart_search.py`) drops prompt field axes when the pipeline has no LLM node with `prompt_meta`.

---

## Key Files

| Concern | File |
|---------|------|
| Field constants | `api/shared/constants.py` |
| OptSearchPoint (render, derive, project) | `api/models/opt_search_point.py` |
| Variant library | `api/config/prompt_variants.json` |
| Variant filtering | `api/services/search/smart_search.py` |
