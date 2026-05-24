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

## First run

**System requirements**

- **Python 3.13+** with `pip`.
- **`.env` with an LLM provider key** at the repo root — one of `GROQ_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`.
- **TermNorm backend** running locally for backends that need it — clone the sibling repo (`TermNorm-excel/backend-api`) and run its `start-server-py-LLMs.bat`. Datasets like `llm_only` and `bbeh` don't require it; pipeline-driven datasets do.
- Recommended: **VS Code + Claude Code** for the `/potter-run` skill experience.

**Three-command path**

```bash
pip install -e ".[all]"
python -m promptpotter new <dataset>     # e.g. llm_only or bbeh
python -m uvicorn promptpotter.main:app --port 8001
# Open http://localhost:8001/ui/
```

The first command installs dependencies. The second mints a campaign and starts the optimization loop in your terminal. The third (in a separate terminal) serves the read-only dashboard webapp. Full walkthrough: [`docs/manual/`](docs/manual/README.md).

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
- **What if it stalls?** Stall and failure are different triggers. Failures route back to the proposing layer ([self-healing](docs/developer/self-healing-internals.md)); stalls escalate L1 → L2 → L3 ([the-loop](docs/concepts/the-loop.md)). Stuck for other reasons: [troubleshooting](docs/manual/05-troubleshooting.md).

## ⭐ Features

- **Prompt + pipeline optimization:** **LLM-driven program evolution** over your prompt AND your pipeline parameters jointly. Most tools optimize one or the other. Head-to-head: [related-work.md](docs/research/related-work.md).
- **Auto-injected scoring:** define your scoring formula once in `campaign.json`. It's wired into every evaluation path automatically. No glue code.
- **IDE-native operation:** drive a full optimization campaign from your terminal via the `/potter-run` Claude Code skill. No notebook required.
- **🔁 Self-healing optimization:** when a proposed setting isn't valid for your task workflow, the verification harness catches it (deterministic) and tells the strategy layer (L2 or L3) what went wrong, which in turn updates the prompt of the model that proposed the invalid setting. Full architecture: [self-healing-internals.md](docs/developer/self-healing-internals.md).
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
| [Self-healing](docs/developer/self-healing-internals.md) | [Persistence, state, recovery](docs/operations/persistence-and-state.md) | [Related work](docs/research/related-work.md) |
| [Scoring and memory](docs/concepts/scoring-and-memory.md) | [Observability](docs/operations/observability.md) | |
| [Campaign tree](docs/concepts/campaign-tree.md) | | |
| [Nodes and pipelines](docs/concepts/nodes-and-pipelines.md) | | |

Developer internals (Python symbols, data contracts, wiring) live under [`docs/developer/`](docs/developer/README.md). Statistical foundations under [`docs/methods/`](docs/methods/README.md).

## Watching a run

While `python -m promptpotter resume` is running, the cleanest setup is **`campaigns/{cycle_id}/dashboard.json` open in an auto-reloading editor + the CLI terminal visible**. `dashboard.json` is the live scalar state (phase, round, candidate, accuracy, in-flight query, per-round node I/O); the CLI prints HIT/MISS lines + per-candidate + per-round banners as they happen. Drill-down peers in the same directory: `output.log`, `rounds/`, `log.md`. Internal resume + audit state lives under `.cache/` (hidden by convention). Alternatives: `/potter-run` Claude Code skill, the notebook, or the planned webapp. Full guide in [`CLAUDE.md`](CLAUDE.md#superuser-monitoring-live-runs).

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

## Scientific framing

PromptPotter is a **tree search over prompt programs**: an LLM proposes prompt variants, your evaluator scores them, and weak branches get pruned early. Algorithmically, it is evolutionary search with [Bayesian best-arm-identification](docs/research/related-work.md#best-arm-identification--sequential-testing) pruning (PoBB) — the same *statistical-confidence-guided tree-search* family as MCTS, but deterministic evaluation in place of random rollouts. Comparison: [`docs/research/related-work.md`](docs/research/related-work.md#comparison-to-mcts).

**Structurally closest peer: AlphaEvolve.** Of the published systems in the LLM-driven-evolution family, **AlphaEvolve** is what PromptPotter sits closest to structurally — same generate → evaluate → select loop with cross-iteration memory, applied to prompts + pipeline parameters instead of source code. Attribute-by-attribute:

| AlphaEvolve attribute | In PromptPotter | How |
|---|:--:|---|
| **Evolutionary search** | 🟢 shipped | Generate / score / select / mutate over a candidate population; explicit `population` + `individual` + `generation` vocabulary throughout the codebase. |
| **Automated evaluation** | 🟢 shipped | `score_search_point()` is the single scoring gateway; per-dataset scoring formula in `campaign.json` is compiled once and injected into every eval path. No human-in-the-loop scoring. |
| **Library learning** | 🟡 partial | PromptPotter's "library" is the cross-cycle **MeasurementArchive** (`archive/measurements/`) + `AxisIndex` / `SampleIndex` / `ConfigIndex` digests — a *measurement* library, not the *program* library AlphaEvolve carries. Informs every L1 mutation via `cycle.axes.digest()` + `sibling_yield`. |
| **Meta-learning** | 🟡 partial | The optimizer can be pointed at its own meta-prompts (L4 outer loop) — concept: [`optimizer-of-the-optimizer.md`](docs/concepts/optimizer-of-the-optimizer.md); the `potter-l1-meta-campaign` skill ships the state machine that evolves `l1_generate` / `l1_critique` / `l2_context` / `l3_plan` over assess → screen → promote cycles. The full L4 loop is planned. |
| **MCTS** | 🟡 selection-signal shipped | L3 emits an observation-only `fork_proposal` ({round_offset, reason}) when it judges the current subtree exhausted; the operator forks manually. Backprop up the lineage + UCB-style ancestor selection + auto-fork are planned (see Aspiration below). |

Full capability table including AlphaEvolve and the prompt-tooling neighbors: [`docs/research/related-work.md#capability-matrix`](docs/research/related-work.md).

**Aspiration — towards AlphaZero-shaped MCTS.** L3 (the strategic replan layer) is gaining MCTS-style selection in three steps. **Step 1 — shipped:** L3 may now emit an observation-only `fork_proposal` (`{round_offset, reason}`) alongside its `plan` rewrite when it judges the current subtree exhausted and a deferred ancestor more promising. The proposal lands in `round_NNNN.json::nodes[l3_plan].exit.fork_proposal`; the operator reads it and forks manually via `resume --from N` if they agree. **Step 2 — planned:** propagate round outcomes as node statistics up the lineage tree. **Step 3 — planned:** UCB-style ancestor-selection rule for automatic L3 forking. The three together = AlphaZero-shaped MCTS, categorically capable of *recovering from dead-end branches* that today the loop can only stall on. Backlog: [`docs/specs/roadmap.md`](docs/specs/roadmap.md#backlog-unscheduled).

[![OpenEvolve: Towards Open Evolutionary Agents](https://img.youtube.com/vi/mWBT-szUutI/hqdefault.jpg)](https://www.youtube.com/watch?v=mWBT-szUutI)

*Background — Asankhaya Sharma on [OpenEvolve](https://www.youtube.com/watch?v=mWBT-szUutI).*

Peer systems in the same family — full comparison + benchmark notes in [`docs/research/related-work.md`](docs/research/related-work.md#eight-systems-under-the-umbrella):

- Code evolution: [AlphaEvolve](docs/research/related-work.md#eight-systems-under-the-umbrella) · [OpenEvolve](docs/research/related-work.md#eight-systems-under-the-umbrella) · [AlgoTuner](docs/research/related-work.md#eight-systems-under-the-umbrella) · [AutoResearch](docs/research/related-work.md#eight-systems-under-the-umbrella)
- Prompt evolution: [PromptWizard](docs/research/related-work.md#eight-systems-under-the-umbrella) · [MIPROv2](docs/research/related-work.md#eight-systems-under-the-umbrella) · [GEPA](docs/research/related-work.md#eight-systems-under-the-umbrella)

# Roadmap

Short version: [`docs/roadmap.md`](docs/roadmap.md). Full development plan with milestones + specs: [`docs/specs/roadmap.md`](docs/specs/roadmap.md).

# Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: LLM-Driven Evolution of Prompts and Pipelines},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/runfish5/prompt-potter-optimizer}
}
```
