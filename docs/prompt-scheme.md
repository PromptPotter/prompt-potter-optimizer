# Prompt Decomposition Scheme

PromptPotter decomposes a monolithic prompt into independent fields so each can be perturbed, measured, and optimized independently. The sensitivity scan tests one field at a time (OAT); the optimization loop mutates fields via LLM-guided generation. Decomposition is the foundation of the entire search strategy.

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

Source of truth: `PROMPT_STRING_FIELDS` in `api/shared/constants.py` (fields 1–6). `few_shot_examples` and `plan` are rendered explicitly after the string fields.

`instruction` has no pre-built variants because it is always LLM-generated — it is the primary mutation surface. `few_shot_examples` is excluded from `PROMPT_STRING_FIELDS` and rendered via its own block formatter. `plan` is set by L3 and rendered last — empty by default (skipped).

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

`render()` (`opt_search_point.py:114`) iterates `PROMPT_STRING_FIELDS` in order, collects non-empty values, appends the few-shot block if present, appends `plan` if present, and joins with `\n\n`. Empty fields are skipped — setting a field to `""` effectively removes it from the rendered prompt.

```python
parts = [v for f in PROMPT_STRING_FIELDS if (v := getattr(self, f))]
```

`compile_prompt()` (`opt_search_point.py:141`) adds a second pass: substitutes `{{variable}}` placeholders with runtime values (Langfuse-compatible).

---

## Type Hierarchy

The 8 field types live on `PromptTemplate`, the base class shared by **all** prompts — both job prompts (the prompt being optimized) and optimizer meta-prompts (L1/L2/L3/Critique templates). `OptSearchPoint` inherits from `PromptTemplate` and adds optimizer-specific state (lineage, L2/L3, memory). Each prompt subtype uses the fields it needs; empty fields are skipped by `render()`.

```
SearchPoint (abstract — render())
    ├── JobSearchPoint (frozen target layer)
    └── PromptTemplate (8 prompt fields + render/compile)
            └── OptSearchPoint (+ lineage, L2/L3, memory)
```

`load_optimizer_prompt()` returns `PromptTemplate` — callers use `.compile_prompt()` to substitute `{{variable}}` placeholders, then pass to `llm_call()`. The type system prevents accidentally accessing optimizer-specific fields (`.escalation_journal`, `.l2_directive`, etc.) on template instances.

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

In optimizer prompts, `problem_description` carries all analytical evidence (eval stats, scan data, pipeline state, critique, escalation). `instruction` carries the task directives (including L2's directive when set). `plan` carries L3's strategic framework.

All optimizer prompts follow the same pattern: JSON template → `load_optimizer_prompt()` → `compile_prompt()` → `llm_call()`. Templates live in `api/config/optimizer_prompts/`.

---

## Projection to Target Pipeline

```
OptSearchPoint              JobSearchPoint                 Backend
                       to_job_search_point()            run_match()
  6 fields ──► render() ──► pipeline_params ──────────► node_config
               │              │                           │
               ▼              ▼                           ▼
          prompt string    {"llm_ranking":             {"node_config":
                            {"prompt": "..."}}          {"llm_ranking":
                                                         {"prompt": "..."}}}
```

Three transformations bridge optimizer state to the wire:

| Step | Function | Input → Output |
|------|----------|----------------|
| 1 | `render()` | 6 prompt fields → single string |
| 2 | `to_job_search_point()` | OptSearchPoint → frozen `JobSearchPoint` with prompt in `pipeline_params[prompt_node]["prompt"]` |
| 3 | `run_match()` | `pipeline_params` dict → TermNorm `node_config` wire payload |

`to_job_search_point()` (`opt_search_point.py:156`) also carries `prompt_fields` (the decomposed dict) on the `JobSearchPoint` for variant derivation without round-tripping through `OptSearchPoint`.

---

## How L1 Mutates Fields

`l1_generate()` (`l1_optimizer.py`) assembles a meta-prompt containing:

- The current rendered prompt (`opt_sp.render()`)
- Critique text OR `l2_directive` (mutual exclusion — directive replaces critique when L2 has fired)
- Scan analytics (parameter sensitivity data)
- `task_context`, `thinking_styles`, `plan`
- `escalation_journal` + `warning_inventory` (probe rounds only — non-probe rounds with a directive skip ESCALATION entirely)

See [`docs/information-flow.md`](information-flow.md) for the full consumer matrix.

The LLM returns candidate dicts. Currently, `instruction_spec` explicitly targets the `instruction` field, but `derive_candidate()` (`opt_search_point.py`) accepts changes to **any** `PROMPT_STRING_FIELDS` field via `**changes`:

```python
for f in PROMPT_STRING_FIELDS:
    data[f] = changes.pop(f, getattr(self, f))
```

Each candidate becomes a child `OptSearchPoint` with `parent_id` lineage. L2/L3 state (optimizer_params, task_context, plan) is inherited; optimization memory (critique, escalation_journal) is **not** — fresh analysis each round.

---

## Variant Library

`api/config/prompt_variants.json` provides pre-built alternatives per field for the sensitivity scan (OAT). Each entry has a `"text"` value and optional metadata (`source`, `year`).

| Field | Variant count | Source |
|-------|--------------|--------|
| `persona` | 6 | Hand-crafted role framings |
| `task_intent` | ~5 | Task description variations |
| `problem_description` | ~5 | Domain context variations |
| `thinking_style` | 35+ | Research literature (CoT, ToT, etc.) |
| `answer_format` | ~5 | Output structure variations |
| `instruction` | — | Always LLM-generated |
| `few_shot_examples` | — | Not in variant library |

Loaded via `api/config/settings.py:84`. `filter_variant_library()` (`smart_search.py:294`) drops prompt field axes when the pipeline has no LLM node with `prompt_meta`.

---

## Dynamic Field Set (Design Vision)

### Current constraint

`PROMPT_STRING_FIELDS` is a static list in `constants.py` (6 string fields). `few_shot_examples` and `plan` are rendered explicitly. The set of 8 fields is fixed at code-definition time.

### Vision

The field set should be **open, not closed**. L2 should be able to:

- **Add fields** — e.g., `domain_constraints`, `output_validation`, `error_handling_rules`, `negative_examples`
- **Remove fields** — e.g., drop `persona` if sensitivity scan shows zero impact

```
L2 REFINE ──► add_field("domain_constraints")
              remove_field("persona")
                    │
                    ▼
┌─ PROMPT SCHEME ──────────────────────────┐
│  1. task_intent                          │
│  2. problem_description                  │
│  3. instruction                          │
│  4. thinking_style                       │
│  5. answer_format                        │
│  6. domain_constraints          ← NEW    │
│                                          │
│  +/- [???]                               │
└──────────────────────────────────────────┘
```

### Why it works architecturally

- `render()` already skips empty fields — **removal is just setting to `""`**
- `derive_candidate()` iterates a field list — making this list dynamic is the key change
- `prompt_field_dict()` similarly iterates the same list
- New fields don't need Pydantic attributes — an overflow `dict[str, str]` handles dynamic additions

### Benefit

L2 field mutations **widen or narrow the search space**. Triggering field add/remove more frequently in L2 means the optimizer explores structurally different prompt shapes, not just content variations within a fixed template. A prompt with 4 fields searches a fundamentally different space than one with 8 fields.

### Open questions

- How does the variant library adapt to new fields? Auto-generate variants via LLM?
- Should sensitivity scan test dynamic fields, or only established ones?
- Field ordering for new fields — append at end, or L2 specifies position?
- Persistence: dynamic fields must serialize/deserialize through `OptSearchPoint` → `JobSearchPoint` → disk

---

## Key Files

| Concern | File |
|---------|------|
| Field constants | `api/shared/constants.py` |
| OptSearchPoint (render, derive, project) | `api/models/opt_search_point.py` |
| L1 candidate generation | `api/services/l1_optimizer.py` |
| L2/L3 transitions | `api/services/campaign/layer_transitions.py` |
| Variant library | `api/config/prompt_variants.json` |
| Variant loading | `api/config/settings.py` |
| Variant filtering | `api/services/search/smart_search.py` |
