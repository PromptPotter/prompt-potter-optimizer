# Prompts and Individuals

PromptPotter doesn't treat a prompt as one opaque string. It decomposes every prompt into independently mutable fields — six prompt-string fields rendered in order, plus two appended sections (few-shot examples, plan). L1 mutates one axis at a time, holding the rest fixed, so the fitness delta of each axis is unambiguous.

The decomposition, render pipeline, and field registry live in [../developer/prompt-scheme-internals.md](../developer/prompt-scheme-internals.md). This page is the *why*.

## Why decomposition matters

**Measurable axes.** Search memory tracks effect size per axis. After enough campaigns, *thinking style* may routinely move fitness by several points on this kind of problem while *persona* barely matters; future rounds spend mutation budget where it pays off.

**Targeted mutation.** L1 mutates one field, holds the rest, scores the delta. The genotype is high-dimensional but each per-round move is one-dimensional, so the signal is clean.

**Recursion.** The optimizer's own meta-prompts (L1, L2, L3, critique) use the same scheme, so the same evolution machinery applies recursively when an outer loop optimises the optimiser.

## Two parameter namespaces

An individual is more than a prompt. It also carries pipeline parameters — thresholds, model names, temperature, retrieval budgets — anything the pipeline's nodes expose. These live in a separate namespace from prompt fields. Names can overlap (*thinking style* may be both a prompt field and a node parameter); they remain independent axes regardless.

L1 mutates both namespaces in the same proposal — "change the persona and bump the web search budget" — with routing handled at individual-creation time.
