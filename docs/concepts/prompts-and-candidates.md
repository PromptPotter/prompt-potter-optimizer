# Prompts and Candidates

PromptPotter doesn't treat a prompt as one opaque string. It decomposes every prompt into eight named fields, each independently tunable. This matters for two reasons: the optimizer can vary one axis without touching the others, and it can measure which axes actually move the needle.

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

Three things fall out of this design.

**Measurable axes.** Search memory tracks effect size per axis. After enough campaigns, the optimizer knows that *thinking style* routinely moves scores by several percentage points on this kind of problem, while *persona* barely matters. Future rounds spend budget where budget pays off.

**Targeted mutation.** L1 doesn't have to rewrite the entire prompt to try a variation. It can pick one field to change, keep the rest, and score the difference. A prompt is a high-dimensional point; each field is one dimension.

**Recursion.** The optimizer's own meta-prompts — the templates driving L1, L2, L3, and the critique step — are themselves prompts, decomposed into the same eight fields. A future outer-loop PromptPotter could optimize the optimizer's prompts using the same machinery it uses on target-backend prompts. The design is self-similar all the way down.

## Two parameter namespaces

A candidate is more than a prompt. It also has pipeline parameters — thresholds, model names, temperature, retrieval budgets, anything the pipeline's nodes expose. These live in a separate namespace from the prompt fields. Some names overlap — *thinking style* might appear both as a prompt field (steering how the LLM reasons) and as a node parameter (selecting among hardcoded strategies in a non-LLM step). They're treated as independent axes regardless.

When L1 proposes a candidate, both namespaces get optimized together. A proposal can be "change the persona and bump the web search budget," with no awareness of which knob lives where. The routing happens automatically at candidate-creation time.

## Two prompt stores

PromptPotter keeps two independent prompt stores, each answering a different question.

**Per-dataset starting points** ship in `datasets/{name}/prompts/`. Each file is a full prompt template — the six canonical fields plus optional few-shot examples and plan. The campaign config picks a variant by name. This is the source of truth for the initial pipeline configuration.

**The crossover pool** ships with the project itself. It's a library of task-agnostic material — dozens of candidate values for each field — that L1 mixes and matches during optimization. It's not a starting-point store; it's the mutation dictionary.

The two stores don't overlap. The first answers *where do we start?*. The second answers *what variations can we try?*.

For the implementation — template classes, rendering pipeline, loader mechanics — see [../developer/prompt-scheme-internals.md](../developer/prompt-scheme-internals.md).
