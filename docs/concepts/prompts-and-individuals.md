# Prompts and Individuals

PromptPotter doesn't treat a prompt as one opaque string. It decomposes every prompt into eight named fields — eight mutation axes. L1 mutates one axis at a time, holding the rest fixed, so the fitness delta of each axis is unambiguous.

---

## The prompt scheme

PromptPotter decomposes your backend's monolithic prompt into independently optimizable fields:

```
┌─ PROMPT SCHEME ──────────────────────────┐
│  1. task_intent                          │
│  2. problem_description                  │
│  3. instruction                          │
│  4. thinking_style                       │
│  5. answer_format                        │
│  6. domain_constraints                   │
│  +/- [???] auto configuring              │
└──────────────────────────────────────────┘
```

### The eight fields

| # | Field | Purpose |
|---|-------|---------|
| 1 | **persona** | Who the LLM is — role framing. "You are a domain expert who…" |
| 2 | **task intent** | What the LLM should accomplish at a high level |
| 3 | **problem description** | Domain context, situational state, analytical evidence |
| 4 | **instruction** | The core prompt template — the primary mutation target for L1 |
| 5 | **thinking style** | Reasoning strategy guidance — chain-of-thought, tree-of-thought, step-by-step, etc. |
| 6 | **answer format** | Output structure constraints — JSON, one word, a number with units |
| 7 | **few-shot examples** | Input/output demonstration pairs, rendered inline |
| 8 | **plan** | A strategic framework appended to the whole prompt; L3 owns this field |

The first six render into a single prompt string in order. Few-shot examples are rendered separately as demonstration pairs. The plan is appended at the end.

Each field can be swapped independently. A prompt with thinking style "chain of thought" can be compared head-to-head with the same prompt under thinking style "step back and verify" — everything else held constant, one axis isolated.

## Why decomposition matters

**Measurable axes.** Search memory tracks effect size per axis. After enough campaigns, *thinking style* may routinely move fitness by several points on this kind of problem while *persona* barely matters; future rounds spend mutation budget where it pays off.

**Targeted mutation.** L1 mutates one field, holds the rest, scores the delta. The genotype is high-dimensional but each per-round move is one-dimensional, so the signal is clean.

**Recursion.** The optimizer's own meta-prompts (L1, L2, L3, critique) are decomposed into the same eight fields, so the same evolution machinery applies recursively when an outer loop optimises the optimiser.

## Two parameter namespaces

An individual is more than a prompt. It also carries pipeline parameters — thresholds, model names, temperature, retrieval budgets — anything the pipeline's nodes expose. These live in a separate namespace from prompt fields. Names can overlap (*thinking style* may be both a prompt field and a node parameter); they remain independent axes regardless.

L1 mutates both namespaces in the same proposal — "change the persona and bump the web search budget" — with routing handled at individual-creation time.

## Two prompt stores

PromptPotter keeps two independent prompt stores, each answering a different question.

**Per-dataset starting points** ship in `datasets/{name}/prompts/`. Each file is a full prompt template — the six canonical fields plus optional few-shot examples and plan. The campaign config picks a variant by name. This is the source of truth for the initial pipeline configuration.

**The crossover pool** ships with the project itself. It's a task-agnostic library — dozens of values per field — that L1 draws from when mutating. Not a starting point; a mutation dictionary.

The two stores don't overlap. The first answers *where do we start?*. The second answers *what variations can we try?*.

For the implementation — template classes, rendering pipeline, loader mechanics — see [../developer/prompt-scheme-internals.md](../developer/prompt-scheme-internals.md).
