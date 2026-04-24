# What is PromptPotter?

PromptPotter finds better prompts automatically. You give it a dataset and a pipeline endpoint — it tries variations of your prompt and pipeline configuration, measures accuracy on each one, and iterates. An AI critique layer analyzes what worked and what didn't. The result is a better-performing prompt and configuration, found without manual trial and error.

The pipeline can be a single LLM call or a multi-step pipeline with caching, retrieval, and ranking. PromptPotter treats it as a black box: it sends inputs, reads outputs, scores results, and adjusts.

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
