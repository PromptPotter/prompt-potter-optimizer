# What is PromptPotter?

PromptPotter finds better prompts automatically. You give it a dataset and a pipeline endpoint — it tries variations of your prompt and pipeline configuration, measures accuracy on each one, and iterates. An AI [critique layer](../concepts/the-loop.md) analyzes what worked and what didn't. The result is a better-performing prompt and configuration, found without manual trial and error.

The pipeline can be a single LLM call or a [multi-step pipeline](../concepts/nodes-and-pipelines.md) with caching, retrieval, and ranking. PromptPotter treats it as a black box: it sends inputs, reads outputs, scores results, and adjusts.

---

## How it stays cheap

💰 Every evaluation costs money. PromptPotter is built to maximize accuracy per dollar:

- **[Search-only-with-evidence](../methods/candidate-elimination.md).** Each candidate runs against a small handful of samples by default (~3–5). Only candidates with statistical evidence of being promising get extended.
- **[Hard-sample dashboard](../methods/exploration-exploitation.md).** Samples everyone aces or everyone fails carry no signal. The optimizer surfaces and scores on the samples that actually separate candidates.

---

## When the optimizer gets stuck

🛟 If progress stalls, PromptPotter doesn't just keep trying the same kinds of variations. An outer loop steps in, looks at what's failing, and **rewrites the framing** the next round uses. If that also stalls, a higher loop **replans the strategy**.

You'll see this in the round summary — the **Layer** field tells you whether the current round is normal, recovering, or replanning.

Internals: [`../concepts/the-loop.md`](../concepts/the-loop.md).

---

## Three concepts

🧱 Everything in this manual uses these three terms (and more — see the [glossary](../concepts/glossary.md)).

**Campaign** — one complete optimization run. A campaign has a fixed dataset, a fixed pipeline endpoint, and a starting prompt. It runs until you stop it or it hits its round limit.

**Round** — one generate-evaluate-critique cycle inside a campaign. Each round, the optimizer proposes several candidate configurations, scores all of them against the dataset, and runs a critique to decide what to try next. A campaign is a sequence of rounds.

**Candidate** — one proposed configuration scored during a round. A candidate is a specific combination of prompt fields and pipeline parameters. The best-scoring candidate that beats the current best becomes the new best; the others are discarded.

---

## Why run it

🎯 Prompt tuning by hand is slow, inconsistent, and doesn't compound. You test a few ideas, something works, you ship it — but you don't know which part of the prompt drove the improvement, and the knowledge is lost when the next project starts.

PromptPotter [accumulates knowledge across runs](../concepts/scoring-and-memory.md). Every evaluation is stored. When you start a new campaign later, the optimizer already knows which parameter regions failed on prior runs, which queries are always easy or always hard, and which prompt axes actually move the needle.

Next: [Install](02-install.md).
