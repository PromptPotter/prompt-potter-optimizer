# Introduction

## What PromptPotter does

Give PromptPotter a dataset and a pipeline endpoint. It will automatically try variations of your prompt and pipeline configuration, measure accuracy on each one, and iterate — guided by an AI critique layer that analyzes what worked and what didn't. The result is a better-performing prompt and configuration, found without manual trial and error.

The pipeline can be a single LLM call or a multi-step pipeline with caching, retrieval, and ranking steps. PromptPotter treats it as a black box: it sends inputs, reads outputs, scores results, and adjusts.

## The problem it solves

Prompt tuning by hand is slow, inconsistent, and doesn't compound. You test a few ideas, something works, you ship it — but you don't know which part of the prompt drove the improvement, and you lose that knowledge when the next project starts.

PromptPotter accumulates knowledge across runs. Every evaluation is stored. When a campaign stalls and you start a new one later, the optimizer already knows which parameter regions failed, which queries are always easy or always hard, and which prompt axes actually move the needle. Each run starts from higher ground than the last.

## Three concepts to know

Before reading anything else, understand these three terms — everything in the documentation uses them.

**Campaign** — one complete optimization run. You start a campaign with `init`, run it with `optimize`, and inspect results with `show-results`. A campaign has a fixed dataset, a fixed pipeline endpoint, and a starting prompt. It runs until you stop it or it hits its round limit.

**Round** — one generate-evaluate-critique cycle inside a campaign. Each round, the optimizer proposes several candidate configurations, scores all of them against the dataset, and runs a critique to decide what to try next. A campaign is a sequence of rounds.

**Candidate** — one proposed configuration scored during a round. A candidate is a specific combination of prompt fields and pipeline parameters. The best-scoring candidate that beats the current baseline becomes the new best; the others are discarded.

## Where to read next

Two paths depending on your goal:

**Understand the system** — start here, then read [`how-a-campaign-runs.md`](how-a-campaign-runs.md) for a complete walkthrough of what happens during `optimize`, then [`architecture/optimization.md`](architecture/optimization.md) for the full mechanics.

**Extend the system** — read [`architecture/node-standard.md`](architecture/node-standard.md) to understand how pipeline nodes are declared and what capabilities they expose, then [`architecture/information-flow.md`](architecture/information-flow.md) to see what data each optimizer layer reads and writes, then [`architecture/prompt-scheme.md`](architecture/prompt-scheme.md) for the prompt field decomposition.
