# What is PromptPotter?

PromptPotter finds better prompts automatically. You give it a dataset and a pipeline endpoint — it tries variations of your prompt and pipeline configuration, measures accuracy on each one, and iterates. An AI critique layer analyzes what worked and what didn't. The result is a better-performing prompt and configuration, found without manual trial and error.

The pipeline can be a single LLM call or a multi-step pipeline with caching, retrieval, and ranking. PromptPotter treats it as a black box: it sends inputs, reads outputs, scores results, and adjusts.

---

## How it stays cheap

Every evaluation costs money. PromptPotter is built to maximize accuracy per dollar:

- **Search-only-with-evidence.** Each candidate runs against a small handful of samples by default (~3–5). Only candidates with statistical evidence of being promising get extended.
- **Hard-sample dashboard.** Samples everyone aces or everyone fails carry no signal. The optimizer surfaces and scores on the samples that actually separate candidates.

---

## The three layers

Inside one round PromptPotter runs three layers, each wrapping the next like a system prompt:

- **L1** generates and scores candidates. It mutates the prompt template's fields (persona, task instruction, …) and the pipeline parameters.
- **L2** writes a **CONTEXT** outline that wraps L1, and modifies L1's fields when L1 stalls.
- **L3** writes a **PLAN** outline that wraps L2, and is rewritten when L2 itself stalls.

CONTEXT and PLAN live on disk in each trial file — the loop's actual config, inspectable and editable.

**How the layers talk.** The layers don't call each other directly — they write to a shared record (one per candidate). When **L2** fires, it leaves a short **directive** that L1 reads in the next round (and overwrites once L1 improves). When **L3** fires, it leaves a **plan** that sticks around until L3 next replaces it. L1 reads both each round.

---

## Three concepts

Everything in this manual uses these three terms.

**Campaign** — one complete optimization run. A campaign has a fixed dataset, a fixed pipeline endpoint, and a starting prompt. It runs until you stop it or it hits its round limit.

**Round** — one generate-evaluate-critique cycle inside a campaign. Each round, the optimizer proposes several candidate configurations, scores all of them against the dataset, and runs a critique to decide what to try next. A campaign is a sequence of rounds.

**Candidate** — one proposed configuration scored during a round. A candidate is a specific combination of prompt fields and pipeline parameters. The best-scoring candidate that beats the current best becomes the new best; the others are discarded.

---

## Why run it

Prompt tuning by hand is slow, inconsistent, and doesn't compound. You test a few ideas, something works, you ship it — but you don't know which part of the prompt drove the improvement, and the knowledge is lost when the next project starts.

PromptPotter accumulates knowledge across runs. Every evaluation is stored. When you start a new campaign later, the optimizer already knows which parameter regions failed on prior runs, which queries are always easy or always hard, and which prompt axes actually move the needle.

Next: [Install](02-install.md).
