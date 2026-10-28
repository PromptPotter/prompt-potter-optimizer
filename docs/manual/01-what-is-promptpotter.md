# What is PromptPotter?

PromptPotter finds better prompts automatically. You give it a dataset and a pipeline — it tries variations of your prompt and pipeline configuration, measures each one, and iterates. An AI critique analyzes what worked and what didn't. The result is a better-performing prompt and configuration, found without manual trial and error.

The pipeline can be a single LLM call or a multi-step pipeline with retrieval and ranking. PromptPotter treats it as a black box: it sends inputs, reads outputs, scores results, and adjusts.

---

## Three concepts

Everything in this manual uses these three terms.

**Campaign** — one complete optimization run. A campaign has a fixed dataset, a fixed pipeline, and a starting prompt (the origin, `C0`). It runs until you stop it or it hits its round limit.

**Round** — one generate-score-critique cycle inside a campaign. Each round, the optimizer proposes several candidate configurations, scores them on samples from the dataset, and runs a critique to decide what to try next. A campaign is a sequence of rounds.

**Candidate** — one proposed configuration scored during a round: a specific combination of prompt fields and pipeline parameters. The candidate that beats its parent (the configuration it was mutated from) becomes the next parent; the others are discarded. PromptPotter's own optimizer decides on ability (θ), which weighs how hard each sample is — so a winner's raw accuracy can be lower than a loser's.

---

## Why run it

Prompt tuning by hand is slow, inconsistent, and doesn't compound. You test a few ideas, something works, you ship it — but you don't know which part of the prompt drove the improvement, and the knowledge is lost when the next project starts.

PromptPotter accumulates knowledge across runs. Every evaluation is stored, and a configuration already scored on a sample is replayed instead of paid for again. It spends on the samples that separate candidates, and stops scoring a candidate once the evidence says it cannot win.

Next: [Install](02-install.md).
