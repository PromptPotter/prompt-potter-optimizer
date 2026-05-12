<p align="center">
  <img src="docs/assets/wizard.jpg" alt="PromptPotter wizard" width="100">
  <img src="docs/assets/promptpotter-wordmark.png" alt="PromptPotter" width="420">

</p>

# PromptPotter: LLM-Driven Evolution of Prompts and Pipelines

**PromptPotter brews better prompts.** Most prompt engineering is manual. PromptPotter automates the generate → score → critique cycle. It tries multiple prompt and pipeline variations together, keeps memory across runs, and recovers automatically when a generated prompt produces broken output. Weak candidates get eliminated early using statistical confidence (population-aware Bayesian best-arm identification — *Posterior-of-Being-Best, PoBB*) so you don't burn LLM budget on losers. Built for RAG pipelines, LLM agents, and multi-step LLM workflows — drop in via CLI, Python SDK, or the `/potter-run` Claude Code skill.

## How to Optimize LLM Prompts in 3 Steps

Works for RAG pipelines, LLM agents, and any multi-step LLM workflow.

Describe your 1️⃣ **task**, drop in a labeled 2️⃣ **dataset**, and 3️⃣ **run the loop**. The task is the goal you want the AI to hit; the dataset is examples of hitting it. Each round, PromptPotter generates variations 🧪, scores them ⚖️, and keeps the winners 🏆. It stops when results plateau. ✨ **Prompt optimized.**

> [!IMPORTANT]
> **New here?** Start with [`docs/manual/`](docs/manual/README.md) — six chapters covering install → first run → reading output → troubleshooting.
>
> **Five ways to run it:** 1) `/potter-run` Claude Code skill · 2) CLI · 3) Python / Jupyter notebook · 4) REST API · 5) WebApp (localhost)

## Why PromptPotter?

Manual prompt tuning is slow, inconsistent, and doesn't compound. PromptPotter automates the loop: it tries variations, measures what works, and remembers across runs. Every measurement costs money, so the design is built to **maximize fitness, minimize spend**:

- **Search-only-with-evidence.** Variants default to a small budget (~3–5 samples) and only get extended when there's statistical evidence they have a chance.
- **Hard-sample leaderboard.** Score preferentially on samples that actually separate variants — samples everyone aces or fails are noise.
- **Cross-run memory.** Every datapoint is stored; the optimizer carries what it learned into the next run.

# How It Works

## The Optimization Loop

PromptPotter is a **critique-guided feedback cycle** for prompt and pipeline tuning. Each round generates candidates, scores them against your dataset, and critiques the results to steer the next round.

**One round:**
- generate
- score
- critique

When the inner layer stalls, an outer layer steps in to redirect — see [the-loop.md](docs/concepts/the-loop.md) for the full mechanics.

## Common questions

- **What does L1 actually mutate?** The prompt template's fields (persona, task instruction, …) plus whatever your `pipeline.json` declares as tunable. See [`state-record.md`](docs/concepts/state-record.md).
- **Where do I get a starting prompt?** Bring one with your dataset (`datasets/{name}/prompts/{node}.json`). Walkthrough: [manual ch. 03](docs/manual/03-first-campaign.md).
- **How do I watch a run?** Open `dashboard.json` in an auto-reload editor + watch the CLI terminal. Full guide: [Watching a run](#watching-a-run) above.
- **My scoring formula was wrong — did I lose results?** No. Traces are facts; scores are policy. The optimizer rescores on load and replays decisions; on divergence, fork. See [`scoring-and-memory.md`](docs/concepts/scoring-and-memory.md).
- **What if it stalls?** Stall and failure are different triggers. Failures route back to the proposing layer ([self-healing](docs/concepts/self-healing.md)); stalls escalate L1 → L2 → L3 ([the-loop](docs/concepts/the-loop.md)). Stuck for other reasons: [troubleshooting](docs/manual/05-troubleshooting.md).

## ⭐ Features

- **Prompt + pipeline optimization:** **LLM-driven program evolution** over your prompt AND your pipeline parameters jointly. Most tools optimize one or the other. Head-to-head: [related-work.md](docs/research/related-work.md).
- **Auto-injected scoring:** define your scoring formula once in `campaign.json`. It's wired into every evaluation path automatically. No glue code.
- **IDE-native operation:** drive a full optimization campaign from your terminal via the `/potter-run` Claude Code skill. No notebook required.
- **🔁 Self-healing optimization:** when a proposed setting isn't valid for your task workflow, the verification harness catches it (deterministic) and tells the strategy layer (L2 or L3) what went wrong, which in turn updates the prompt of the model that proposed the invalid setting. Full architecture: [self-healing.md](docs/concepts/self-healing.md).
- **Statistical early-stopping:** unfit candidates are eliminated after a handful of queries — population-aware joint posterior, stop when `P(c is best) < ε` — instead of burning the full budget (*Posterior-of-Being-Best, PoBB*). Methods: [candidate-elimination.md](docs/methods/candidate-elimination.md).
- **Cross-run learning:** every fitness measurement flows into a shared memory store. Parameter impact, query difficulty, and failure patterns are remembered. The optimizer carries what it learned into the next run.

## Limitations

- **Parameter-based optimization only.** PromptPotter optimizes any pipeline that exposes tunable parameters (prompts, thresholds, model settings). It cannot optimize internal model weights, neural architectures, or modality-specific representations (e.g. image embeddings, DNA sequences).
- **Requires a labeled dataset.** Input/output pairs are mandatory.

## Benchmarks

[![PromptWizard](https://img.shields.io/badge/inspired_by-PromptWizard-blue)](https://arxiv.org/abs/2405.18369)
[![BBEH](https://img.shields.io/badge/benchmark-BBEH-purple)](https://github.com/google-deepmind/bbeh)
[![DSPy](https://img.shields.io/badge/compared_against-DSPy-green)](https://github.com/stanfordnlp/dspy)
[![CAPO](https://img.shields.io/badge/compared_against-CAPO-orange)](https://arxiv.org/abs/2504.16005)

Head-to-head comparison on the *BIG-Bench Extra Hard (BBEH)* benchmark against DSPy optimizers (GEPA, MIPROv2, BootstrapFewShot) and CAPO. Same model (`gpt-oss-120b`), same dataset splits, same scoring, no cross-paper number mixing. See [`docs/research/benchmarks.md`](docs/research/benchmarks.md) for results and [`docs/research/bbeh-comparison/`](docs/research/bbeh-comparison/) for reproducible Colab notebooks.

Compared head-to-head with DSPy (GEPA, MIPROv2, BootstrapFewShot), CAPO, and PromptWizard. See [related work](docs/research/related-work.md).

## Documentation

| 🧠 Concepts | ⚙ Operations | 🔬 Research |
|---|---|---|
| [Three-layer loop](docs/concepts/the-loop.md) | [CLI reference](docs/operations/cli-reference.md) | [Benchmarks](docs/research/benchmarks.md) |
| [State record](docs/concepts/state-record.md) | [Backend integration](docs/operations/backend-integration.md) | [Metrics (HC, SE, R₉₀)](docs/research/metrics.md) |
| [Self-healing](docs/concepts/self-healing.md) | [Persistence, state, recovery](docs/operations/persistence-and-state.md) | [Related work](docs/research/related-work.md) |
| [Scoring and memory](docs/concepts/scoring-and-memory.md) | [Observability](docs/operations/observability.md) | |
| [Campaign tree](docs/concepts/campaign-tree.md) | | |
| [Nodes and pipelines](docs/concepts/nodes-and-pipelines.md) | | |

Developer internals (Python symbols, data contracts, wiring) live under [`docs/developer/`](docs/developer/README.md). Statistical foundations under [`docs/methods/`](docs/methods/README.md).

## Watching a run

While `python -m promptpotter optimize` is running, the cleanest setup is **`campaigns/{cycle_id}/dashboard.json` open in an auto-reloading editor + the CLI terminal visible**. `dashboard.json` is the live scalar state (phase, round, candidate, accuracy, in-flight query, per-round node I/O); the CLI prints HIT/MISS lines + per-candidate + per-round banners as they happen. Drill-down peers in the same directory: `output.log`, `rounds/`, `log.md`. Internal resume + audit state lives under `.cache/` (hidden by convention). Alternatives: `/potter-run` Claude Code skill, the notebook, or the planned webapp. Full guide in [`CLAUDE.md`](CLAUDE.md#superuser-monitoring-live-runs).

PromptPotter's inner **generate → score → critique** loop mirrors the classic **plan / implement / validate (PIV)** developer workflow, driven by an LLM at scale.

> [!TIP]
> <details>
> <summary><b>What a round actually looks like</b> (click to expand)</summary>
> 
> ```
> round 3/10 · 5 candidates · sp_budget_ttest=40
> ├─ c0  seed                             acc=0.62  [origin]
> ├─ c1  +thinking_style:step-by-step     acc=0.74  ✓
> ├─ c2  +thinking_style:socratic          acc=0.71
> ├─ c3  +persona:domain expert           acc=0.68  ✗ eliminated @ q18 (t-test)
> └─ c4  model:gpt-oss-120b→… ⚠ invalid   acc=0.00  ↳ validation_failure
>                                                      → L2 brief next round
> 
> winner: c1  (+12pp over origin, p=0.003)
> L1 critique: "Step-by-step improves multi-hop reasoning. Socratic overlaps
>               but adds no marginal gain. Persona drift hurt format compliance."
> ```
> 
> </details>

## Relation to Karpathy's AutoResearch

If your use case is the simple one — a single agent editing one Python file against a fixed 5-min training run, no population, no statistics — go use [`karpathy/autoresearch`](https://github.com/karpathy/autoresearch). It is purpose-built and minimal.

Otherwise: PromptPotter is the same idea generalized. The architecture maps cleanly:

- AutoResearch's `train.py` (the artifact mutated each loop) ≈ PromptPotter's `OptSearchPoint` (the structured artifact L1 mutates each round — `PromptTemplate` fields + `pipeline_params`).
- AutoResearch's `program.md` (agent meta-instructions) ≈ PromptPotter's L1 generate node prompt at [`promptpotter/application/optimization/nodes/l1/generate.py`](promptpotter/application/optimization/nodes/l1/generate.py) — the instructions telling the proposing LLM *how* to mutate. Karpathy's [issue #314](https://github.com/karpathy/autoresearch/issues/314) (evolve `program.md` itself) is the L4 / self-optimization direction PromptPotter has on its own roadmap ([`docs/specs/m12-plus-backlog.md`](docs/specs/m12-plus-backlog.md) § Self-optimization).

Structurally, **AutoResearch is the degenerate case of PromptPotter**:

- `n_variants = 1`
- L2 / L3 / critique disabled
- Elimination disabled (single noisy trial accepted as-is)
- One sample, one scoring run = the 5-min training loss
- Single PromptTemplate field, e.g. `program_md`

It is *not literally* a configuration of PromptPotter today — running AutoResearch's workload on PromptPotter would require a `CodeExecutionConnector` (M12 multi-connector work). With that connector, PromptPotter strictly subsumes AutoResearch and adds population search, Bayesian Posterior-of-Being-Best elimination across seeds, L2/L3 escalation, self-healing, and the hard-sample sorter on top.

| | AutoResearch | PromptPotter |
|---|---|---|
| Evolved artifact | Python source code (`train.py`) | Structured prompt fields + `pipeline_params` |
| Fitness signal | 5-min nanochat training loss | Dataset accuracy (per-sample, scorer formula) |
| Search | 1 agent, try-keep-revert | Population (`n_variants`), PoBB-eliminated rounds |
| Loop layers | Flat — one agent, one loop | L1 generate/critique + L2 refine + L3 replan |
| Recovery | None — agent reverts on regression | Self-healing (`ValidationFailure` / `RuntimeFailure`) per candidate |
| Sample selection | Fixed nanochat run | Rasch + KG scoring-set evolution; hard-sample sorter |
| Statistical guarantees | None — single noisy trial | Bayesian Posterior-of-Being-Best (population-aware best-arm-ID) |
| Domain | ML training research | Prompt/pipeline optimization for production LLM apps |

# Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: LLM-Driven Evolution of Prompts and Pipelines},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/runfish5/prompt-potter-optimizer}
}
```
