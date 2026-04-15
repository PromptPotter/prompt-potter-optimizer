# Benchmarks II — Background & Knowledge Base

> Deep reference material. Part I ([`benchmarks.md`](benchmarks.md)) is the curated, publication-facing doc. This file exists so Claude can pull deeper context when discussing benchmarks, without bloating the main doc.

## BBEH — Task Structure

The 23 BBEH (Big-Bench Extra Hard) tasks are **not strictly ordered by difficulty**. They are categorized by the cognitive domain / skill type they test. Numbering is organizational, not a ladder from easiest to hardest.

### Task clusters

1. **Linguistic & Semantic** — synonyms, antonyms, word analogies. Baseline difficulty; modern models usually handle these with high accuracy.
2. **Logical & Mathematical Reasoning** — boolean logic, arithmetic, sequence completion. Moderate; difficulty spikes with larger numbers or longer logic chains.
3. **Commonsense & World Knowledge** — physical trajectories, social situations. High for small models — requires world modeling, not just text prediction.
4. **Algorithmic & Symbolic** — shuffled-object tracking (the "shell game"), complex grid navigation. Highest difficulty; biggest gap between standard and reasoning-specialized models.

### Why "order" is deceptive

Raw benchmark data sometimes shows higher-numbered tasks scoring lower, but that's usually a coincidence of how tasks were added to the repository — not a designed difficulty ramp.

**Emergence factor:** BBEH difficulty is largely a function of model *scale*. A task a small model scores 0% on can jump to 90% once the model crosses a size threshold. Researchers look at the per-task difficulty curve, not a 1–23 ranking.

### Practical implication for PromptPotter

When interpreting per-task BBEH results, don't read task index as difficulty. Group by cluster (linguistic / logical / commonsense / algorithmic) when analyzing where PromptPotter's L1→L2→L3 loop helps most — the algorithmic/symbolic cluster is where reasoning-model gains are largest and where prompt-level interventions have the most headroom.

## HotpotQA — SOTA & gpt-oss-120b expectations

HotpotQA is a multi-hop QA benchmark with ~90k train / 7.4k dev split and official evaluation for both answer spans and supporting facts. Two settings: **distractor** (gold + distractor paragraphs provided) and **fullwiki** (open-domain retrieval over all of Wikipedia).

### Current leaderboard leaders (as of 2026-04-15 web check)

**Distractor setting** — Beam Retrieval (single model):

| Metric | Score |
|---|---|
| Answer EM | 72.69 |
| Answer F1 | 85.04 |
| Supporting-fact EM | 66.25 |
| Supporting-fact F1 | 90.09 |
| Joint EM | 50.53 |
| Joint F1 | 77.54 |

**Fullwiki setting** — AISO (single model):

| Metric | Score |
|---|---|
| Answer EM | 67.46 |
| Answer F1 | 80.52 |
| Supporting-fact EM | 61.17 |
| Supporting-fact F1 | 86.02 |
| Joint EM | 44.87 |
| Joint F1 | 72.00 |

Source: HotpotQA homepage leaderboard.

### gpt-oss-120b on HotpotQA

**No published HotpotQA-specific score found** for `gpt-oss-120b`. No direct head-to-head against leaderboard numbers is available.

Model-card signals (general reasoning / knowledge strength):

| Benchmark | Score |
|---|---|
| GPQA Diamond | 80.1 |
| MMLU | 90.0 |
| SWE-Bench Verified | 62.4 |
| Codeforces Elo (high reasoning) | 2463 |

HotpotQA is retrieval-heavy and multi-hop, so actual performance depends heavily on whether supporting documents are provided and what retrieval stack is used. The model card notes that browsing/tool use improves factuality — relevant for open-domain QA.

### Practical read for PromptPotter

- For **SOTA reference** on HotpotQA: cite the leaderboard numbers above.
- For **"is gpt-oss-120b good enough?"**: yes as a strong baseline, but expect it to trail specialized HotpotQA systems unless paired with a tuned retrieval stack or fine-tuned.
- For PromptPotter's HotpotQA saturation probe (M10 Wave 1): headroom under `gpt-oss-120b` almost certainly exists in the fullwiki / retrieval-coupled setting; in the distractor setting headroom will be tighter because the hard work (retrieval) is already done.
