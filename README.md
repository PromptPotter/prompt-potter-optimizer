<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/potter.jpg" alt="The Potter" width="64">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/wordmark.png" alt="PromptPotter" width="300">
</p>

<h1 align="center">LLM-driven evolution of prompts and pipelines</h1>

<p align="center">
  Hand it a labeled dataset and a pipeline. It evolves the prompt <b>and</b> the pipeline's parameters together,<br>
  cuts a losing candidate the moment the evidence says it will not win, and shows you every measurement behind the winner.
</p>

<p align="center">
  <a href="https://pypi.org/project/promptpotter/"><img src="https://img.shields.io/pypi/v/promptpotter" alt="PyPI"></a>
  <a href="https://pypi.org/project/promptpotter/"><img src="https://img.shields.io/pypi/pyversions/promptpotter" alt="Python"></a>
  <a href="https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="License"></a>
</p>

<p align="center">
  <b>Try it without installing:</b> <a href="https://promptpotter.com"><b>promptpotter.com</b></a> — <b>10 free optimization runs</b> on your own data, up to 10 rounds each, in the browser and on my key. Offer stands until <b>3 Dec 2026</b>; bring-your-own-key is in the works.
</p>

<p align="center">
  <i>What it does differently:</i> &nbsp;
  <a href="#one-round-replayed">Losers cut early</a> ·
  <a href="#it-moves-the-pipeline-not-just-the-words">Pipeline, not just prompt</a> ·
  <a href="#the-budget-goes-where-candidates-separate">Budget</a> ·
  <a href="#it-does-not-grade-its-own-homework">No self-grading</a> ·
  <a href="#a-campaign-is-an-object-on-disk">Campaign on disk</a>
  <br>
  <i>Try it, then read on:</i> &nbsp;
  <a href="#quickstart">Quickstart</a> ·
  <a href="#the-whole-app-in-one-walk">App tour</a> ·
  <a href="#how-the-search-works">How the search works</a> ·
  <a href="#where-it-sits">vs GEPA / AlphaEvolve</a> ·
  <a href="#the-bench">The bench</a> ·
  <a href="#five-ways-in">Five ways in</a> ·
  <a href="#hack-on-it">Hack on it</a> ·
  <a href="#going-deeper">Going deeper</a> ·
  <a href="#documentation">Docs</a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/hero-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/hero-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/hero-dark.png" alt="The PromptPotter control plane: a finished campaign in the chat view, with its pipeline, its rounds, the trend, and the hard-sample ranking" width="100%">
  </picture>
</p>

<p align="center"><sub>
  The control plane on a real, finished campaign — seven rounds on JustLogic. Nothing here is staged: the thread itself reports that round 6 <i>resolved nothing</i>.<br>
  Every figure on this page is an in-campaign reading from a small run, not a benchmark result.
</sub></p>

## One round, replayed

**Prompt optimization that stops paying for losers.** Every measurement costs money, so the whole design is *most fitness per dollar*.

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-replay.svg" alt="Replay of one real round: three candidates measured, the third eliminated after 7 of 34 samples, the first selected." width="880">
</p>

<p align="center"><sub>
  One round of a real campaign, replayed line for line from its <code>readout.log</code> — the gutter is the source line number. Candidate 3 is cut after <b>7 of 34</b> samples; the other 27 are never bought.
</sub></p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/round-light.svg" alt="One round: a parent is mutated into three candidates, each is measured sample by sample, the third is cut after 7 of 34 samples, and the winner becomes the next parent." width="880">
  </picture>
</p>

Each generation, an LLM mutates the parent into a small population. Every individual walks the same samples, and after each one a posterior asks a single question: *what is the probability this candidate ends up the round's best?* Below the threshold, measurement stops — [Posterior-of-Being-Best](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md). The survivor is elected and breeds the next generation. The drawing is the replay above, read out of the same log.

Four more claims follow, each with the screen that backs it.

<p align="right"><sub><a href="#llm-driven-evolution-of-prompts-and-pipelines">↑ map</a></sub></p>

### It moves the pipeline, not just the words

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/runcard-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/runcard-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/runcard-dark.png" alt="The run card in the chat thread: trend per round, spend, which prompt fields and node parameters the best candidate changed, and how many samples flipped against the origin" width="100%">
  </picture>
</p>

Model, temperature, node thresholds, the output schema — whatever your `pipeline.yaml` declares tunable — are part of the individual, so they mutate and are selected jointly with the prompt fields. In the round replayed above, two of the three mutations were to a schema field description, not to the prompt. The run card says which prompt fields **and which node parameters** the best candidate changed, what it cost, and — on the cells both were measured on — how many samples it now gets right, and wrong, against the origin.

### The budget goes where candidates separate

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/leaderboard-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/leaderboard-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/leaderboard-dark.png" alt="The hard-sample leaderboard: samples ranked by information gain, each with its fitted difficulty and how often it has been graded" width="100%">
  </picture>
</p>

Samples everyone aces or everyone fails are noise, and paying for them buys nothing. The hard-sample leaderboard orders the dataset by how much each sample tells you about which candidate is better, with a fitted difficulty δ beside it, and scoring goes preferentially to those. Measurements are content-addressed: a configuration already scored on a sample is replayed, never re-bought — and the replay still shows its full cost on the ledger.

<table>
<tr>
<td width="50%" valign="top">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/cell-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/cell-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/cell-dark.png" alt="One measured cell opened: prediction, ground truth, the exact prompt sent to the node, and whether it was replayed from the archive" width="100%">
  </picture>
  <br>
  <b>Every number opens down to the call.</b> One cell = one candidate on one sample: prediction, ground truth, the exact input each node received, tokens, seconds — and whether it was paid for or replayed from the archive.
</td>
<td width="50%" valign="top">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/candidates-dark.png" alt="The candidates chart: every candidate of the campaign with accuracy, ability and overlap, intervals drawn, and a served caveat above it" width="100%">
  </picture>
  <br>
  <b>Every candidate, with its interval.</b> Accuracy, ability θ and the shared-cell overlap for each individual across all generations, with the lineage beneath. The caveat above the chart is served by the backend, not decided in the browser.
</td>
</tr>
</table>

### It does not grade its own homework

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/warnings-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/warnings-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/warnings-dark.png" alt="Optimizer warnings: rounds 3 to 6 each resolved nothing, because every arm's lift interval spans zero" width="100%">
  </picture>
</p>

An optimizer that picks its own winner is the easiest thing in this field to fake: with three arms and no real difference between them, *some* arm scores above its parent most of the time. PromptPotter still promotes the best of what it saw — and tells you, per round, on screen and on disk, that the lift interval spans zero. The terminal replay does the same in its own words: its pick is printed with `p=0.42 (ns)` beside it.

Three guards sit behind that. Scores are a **subset-invariant ability** (Rasch θ), so a candidate that drifted onto easier samples cannot out-rank an honest one. **Degenerate candidates** — a constant answer, a shape that games the scorer — are caught before they count. And the layer that **validates** a fix is never the layer that proposed it. [What a winner's number may claim.](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md)

### A campaign is an object on disk

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/files-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/files-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/files-dark.png" alt="The files view: the cycle's round files on disk, with round 6 open as a scoreboard and its verdict" width="100%">
  </picture>
</p>

Not a script that has to finish. The file tree is the dashboard: each round is a JSON file you can open in an editor, and the app renders the same file as a scoreboard. Pause a campaign, resume it, rewind to any round, or fork a sibling and compare:

```bash
python -m promptpotter resume --from 3              # rewind in place
python -m promptpotter resume --fork-on-divergence  # sibling cycle at the divergence point
```

Changing the scoring formula loses no results — traces are facts, scores are policy — and every artifact stamps the ledger position it was written at, so any past moment is reconstructible ([how](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/stable-api.md#the-cut)).

### The whole app in one walk

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tour-dark.gif" alt="A walk through the app: the chat thread, the run card, the dashboard, the sample leaderboard, one opened cell" width="100%">
</p>

<p align="center"><sub>
  The thread, the run card, the optimizer's own loop, the sample leaderboard, one measured cell opened. 📺 Longer: <a href="https://www.youtube.com/watch?v=DLhb26ppX_s">Live #2 — a Swiss-invoice campaign, run raw</a>.
</sub></p>

<p align="right"><sub><a href="#llm-driven-evolution-of-prompts-and-pipelines">↑ map</a></sub></p>

## Quickstart

<p align="center">
  <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/potter-loop.jpg" alt="The Potter driving a failing pipeline through the loop into a verified one" width="400">
</p>

Python 3.13+ and an [OpenRouter key](https://openrouter.ai/keys) — the optimizer's default provider.

```bash
pip install "promptpotter[all]"
python -m promptpotter new tickets.csv --set task_description="classify support tickets by urgency"
python -m promptpotter resume
```

Bring a **labeled dataset** — input/output pairs as CSV, TSV, JSON, JSONL or `.xlsx` — and say what the job is. No key set yet? `new` offers to write one on first run. `resume` picks up wherever the last run stopped: Ctrl+C pauses a campaign rather than losing it.

> [!IMPORTANT]
> **`new` needs a pipeline backend to score against** — the default is [TermNorm](https://github.com/runfish5/TermNorm-excel) on `:8000`, and a campaign refuses to start without one ([setting it up by hand](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/03-first-campaign.md#no-backend)).
>
> **Shortest path:** in [Claude Code](https://claude.com/claude-code), type `/potter-run`. It downloads and starts that backend for you, audits your setup, picks a dataset, runs init, and reads the rounds back to you as they land.

To watch it in the app shown above:

```bash
python -X utf8 -m uvicorn promptpotter.main:app --port 8001    # then open http://localhost:8001/
```

Already in DSPy? It installs where you would reach for GEPA — what you keep and what you trade away that way is [`dspy-optimizer.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md):

```python
from promptpotter.presentation.teleprompter import PromptPotterOpt

optimizer = PromptPotterOpt(metric=my_metric, dataset_name="my-task")
compiled = optimizer.compile(my_program, trainset=trainset)
```

Six chapters from install to troubleshooting: [the manual](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/README.md).

<p align="right"><sub><a href="#llm-driven-evolution-of-prompts-and-pipelines">↑ map</a></sub></p>

## How the search works

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/loop-light.svg" alt="The loop: a task and a labeled dataset enter an optimizer setup, then generate, score and critique repeat until a stopping rule fires. L2 and L3 escalate lazily on a stall; a measurement archive replays instead of re-measuring." width="760">
  </picture>
</p>

[One round](#one-round-replayed) is the unit; a campaign is rounds under three layers, and a higher layer constrains the lower one rather than replacing it:

| Layer | Fires | What it does |
|---|---|---|
| 🧬&nbsp;**L1&nbsp;·&nbsp;generate, evaluate, critique** | every&nbsp;round | Mutates a generation of individuals, measures each one's fitness on your dataset, and reads the raw per-sample misses to steer the next generation. |
| 🔭&nbsp;**L2&nbsp;·&nbsp;refine&nbsp;context** | when&nbsp;L1&nbsp;stalls | Rewrites *how* L1 searches: which evidence it sees and how hard it explores. |
| 🧭&nbsp;**L3&nbsp;·&nbsp;modify&nbsp;plan** | when&nbsp;L2&nbsp;stalls | Rewrites the strategic plan L1 works within. |

The loop exits on a **stopping rule** — goal reached, budget spent, or a plateau — not on a fixed trial count. The critique-and-refine pattern is inspired by [PromptWizard](https://arxiv.org/abs/2405.18369). Full mechanics: [`the-loop.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-light.svg">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/tree-light.svg" alt="A search tree: origin, rounds 1 and 2, then a branch through rounds 3 and 4 that is spent. The search rewinds to round 2 and a second branch reaches the winner." width="880">
  </picture>
</p>

**It searches a tree, not a trail.** Each round's result flows back to every ancestor it descends from, and when a branch is spent a UCB rule picks the ancestor to re-expand — so the search climbs a different hill instead of stalling. An ancestor's score reflects what re-expanding from it actually yielded, including in branches it never ran itself. This is AlphaZero-shaped MCTS over the lineage, with deterministic evaluation on your dataset in place of rollouts: [the four-phase mapping](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md#comparison-to-mcts).

<p align="right"><sub><a href="#llm-driven-evolution-of-prompts-and-pipelines">↑ map</a></sub></p>

## Where it sits

PromptPotter belongs to the **LLM-driven evolution** family — an LLM proposes variants, a scorer ranks them, the winners breed. On the prompt side that family is [GEPA](https://arxiv.org/abs/2507.19457), [MIPROv2](https://arxiv.org/abs/2406.11695), [PromptWizard](https://arxiv.org/abs/2405.18369) and [CAPO](https://arxiv.org/abs/2504.16005); on the code side AlphaEvolve and OpenEvolve. GEPA evolves the instruction text of a DSPy program, AlphaEvolve evolves source code, **PromptPotter evolves a whole declared pipeline jointly with the prompt** — a different target, not a missing feature.

The table is capability, not a benchmark result:

| Capability | GEPA | AlphaEvolve | PromptPotter |
|---|:--:|:--:|:--:|
| **Tunes the whole pipeline** — model, temperature and node thresholds evolve alongside the prompt | 🟡 | 🟡 | 🟢 |
| **Stops losers early** — scores share one scale, so nobody wins by drawing an easier set | 🟡 | 🟡 | 🟢 |
| **Every measurement is priced** — a cache-served result keeps its full cost on the ledger | 🔴 | — | 🟢 |
| **Self-healing** — an invalid proposal is caught and taught to a *different* layer, not just discarded | 🟡 | 🟡 | 🟢 |
| **Carries knowledge across runs** — parameter impact, sample difficulty and failure patterns survive the campaign | 🟡 | 🟢 | 🟢 |
| **Open and inspectable** — a browser control plane, and a campaign you pause, resume, rewind or fork | 🟡 | 🔴 | 🟢 |
| **Runs in your editor** — drive a whole campaign from the terminal (`/potter-run`) | 🔴 | 🔴 | 🟢 |

<sub>🟡 is a partial version of the same capability; AlphaEvolve's are that capability at the code level; `—` is a closed service whose behaviour is not documented. GEPA keeps a candidate pool <i>within</i> a run — its 🟡 on knowledge is about what survives it. One documented exception to the pricing row: a DSPy call replayed from DSPy's own cache is not counted.</sub>

**Where the others are ahead.** GEPA has reach — it lives inside DSPy, with that ecosystem's adapters, tracing and audience, which is why PromptPotter also installs as a DSPy optimizer. AlphaEvolve optimizes code, which PromptPotter is not pointed at. Opik, the closest *product* rival, ships six optimizers behind one API today and tunes sampling parameters too. Langfuse has production telemetry.

> [!TIP]
> **The full argument is one page: [Related work — the landscape, and where the bench sits](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md).** Telemetry vs. optimizer menus vs. single algorithms, the line from APE and OPRO to GEPA, and every row of the table above with its footnotes.

### The bench

Peers ship an algorithm. PromptPotter ships an algorithm *and* the interface around it — measurement, the content-addressed archive, one spend ledger, persistence, the control plane — and the direction of travel is to run **other people's optimizers inside that interface**, as manifests. A menu of optimizers makes them reachable; it does not make their numbers comparable. What has to be held equal before two methods' numbers may sit in one table:

| Held equal | What it rules out |
|---|---|
| **One grader** | each method carrying its own metric function, which drifts |
| **One budget meter** | a method's internal rollouts going unbilled, so cost-per-fitness is a claim rather than a quantity |
| **One exam, chosen before anybody proposes** | a method picking the questions it is graded on |
| **Ability, not accuracy** (Rasch θ) | a method out-ranking an honest one by drifting onto easier samples |
| **A stopping rule** (PoBB) | a fixed trial count standing in for evidence |

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/verdict-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/verdict-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/verdict-dark.png" alt="The compare view's verdict: what was held equal across the compared campaigns, and what stays confounded" width="100%">
  </picture>
</p>

Put campaigns side by side and the first thing served is that list — what was held equal, and what stays confounded. The numbers come after.

**Status, exactly:** manifests for `potter`, `capo`, `levi` and `gepa` are in the tree ([`promptpotter/assets/optimizers/`](https://github.com/PromptPotter/prompt-potter-optimizer/tree/main/promptpotter/assets/optimizers)). The milestone closes when one peer — CAPO first — has run end to end in a real campaign, in a head-to-head whose only difference is the optimizer. That has not happened yet, and no comparison number is published before it does. Design and status: [`roadmap.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md) § The optimizer plug point · the argument: [a menu is not a bench](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#a-menu-is-not-a-bench--the-honest-version).

<p align="right"><sub><a href="#llm-driven-evolution-of-prompts-and-pipelines">↑ map</a></sub></p>

## Five ways in

| | |
|---|---|
| **Webapp** | The browser control plane on this page, at the domain root — [promptpotter.com](https://promptpotter.com) hosted, `http://localhost:8001/` local. Chat-first: talk to the Potter and watch it work inline, with a button whenever a decision is yours. Ships as a reusable chat-app template ([spec](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/chat-foundation.md)). |
| **`/potter-run`** | Claude Code skill — an agent launches, supervises and diagnoses a full campaign from your editor. |
| **CLI** | `python -m promptpotter new <name>` / `resume`. |
| **REST API** | Every control verb the webapp issues, for when another system drives the campaign. |
| **Embedded in Python** | A host program drives one campaign inside its own event loop; the [DSPy optimizer](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/dspy-optimizer.md) is the simple-case entry. |

A sixth — PromptPotter as a tool another agent calls (MCP) — is open for discussion in [#27](https://github.com/PromptPotter/prompt-potter-optimizer/issues/27). Opinions welcome.

## Hack on it

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/optimizer-pipeline-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/optimizer-pipeline-light.png">
    <img src="https://1tw5toebpxy09cvq.public.blob.vercel-storage.com/readme/optimizer-pipeline-dark.png" alt="The optimizer's own pipeline as the dashboard draws it: adaptive_queue, l1_generate, pobb, l1_score, with l2_context, l3_plan and escalation beneath" width="640">
  </picture>
</p>

The optimizer is itself a pipeline of nodes — the same graph the dashboard draws — so it can be pointed at its own optimizer prompts ([self-optimization, L4](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/l4-outer-loop.md)).

```bash
git clone https://github.com/PromptPotter/prompt-potter-optimizer && cd prompt-potter-optimizer
pip install -e ".[all,dev]"
python scripts/gate.py          # every check CI runs
```

The engine is four seams — prompt structure, dispatch, the scoring node, cross-run memory — and **[the developer guide](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/README.md)** draws them on one page. Every optimizer runs end to end at zero spend ([`offline-run.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/offline-run.md)), so a change can be proven before it costs anything. The shape every PR is measured against: [`architecture.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/architecture.md) §0. A recipe per expansion point: [`adding-a-surface.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/adding-a-surface.md). House rules: [`conventions.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/conventions.md).

<p align="right"><sub><a href="#llm-driven-evolution-of-prompts-and-pipelines">↑ map</a></sub></p>

## Going deeper

<details>
<summary><b>Reading a run</b> — what lands on disk, and what a number may claim</summary>

<br>

While a campaign runs, keep **`cycles/{cycle_id}/dashboard.json`** open in an auto-reloading editor beside the terminal: the file is live scalar state (phase, round, candidate, accuracy, in-flight query), the terminal prints HIT/MISS and per-round banners. Drill-down peers sit beside it — `rounds/`, `log.md`, `index.json`, and `readout.log`, the terminal mirrored to a file. Full guide: [reading the output](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/04-reading-the-output.md).

Every cycle writes a per-round digest to `log.md`. Verbatim, from the round replayed at the top — what the artifact looks like, not a result to read a number off:

```
- improved: **yes**
- samples: 34
- composite_fitness: `0.4237`
- ability θ: `+0.308` (ruler 5cd26f22e62708a4, 68 cells, 1PL)
- overlap: 34 shared cells, +16 measured: C0 41.2%  →  C1.2 44.1%  →  C4.1 52.9%
- verdict: C4.1 won on θ lift +0.216 = +0.072 over the parent + 0.144 parent selection bias (θ +0.293 vs parent +0.221, se 0.196); runner-up C4.2 at +0.149
- cost: **$0.0311** (optimizer $0.0048 c23% ·w4384 · backend $0.0137 c17% · bench $0.0127 c37%)

P(best) trajectory:
  f8356ad77a ▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅▅   50.0% [winner]
  6f7d643a1e ▇▆▅▅▅▅▅▅▅▄▅▄▅▄▅▅▅▅▅▅▅▅▅▅▅▅▅▅▄   49.7%
  a2169d009a ▂▂   15.8%
```

A percentage in a round banner is an **in-campaign reading, computed on the rows that selected the winner** — not a held-out result, and a level should never be compared across rounds. A round is won on θ, not on the accuracy column, and the verdict line keeps the lift over the parent apart from the parent's selection bias instead of adding them up quietly. Which figures are clean, which are biased upward, and what we do about it: [`benchmarks.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md).

</details>

<details>
<summary><b>More that ships</b> — self-optimization, block library, memory across runs</summary>

<br>

- **Optimizes itself** — an outer campaign whose individuals are configurations of the inner optimizer. [L4](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/l4-outer-loop.md)
- **Pick your block library mode** — proven personas, thinking styles and answer formats (from PromptWizard and the *Self-Discover* modules it draws on, plus what our own runs turned up). Suggest from the library, restrict to it, or switch it off.
- **Memory across runs** — parameter impact, sample difficulty and failure patterns survive the campaign that found them. [scoring and memory](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md)

</details>

<details>
<summary><b>Common questions</b></summary>

<br>

- **What does L1 actually mutate?** The prompt template's fields (persona, task instruction, …) plus whatever your `pipeline.yaml` declares as tunable. See [`the-loop.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md#the-state-record--what-one-round-carries-forward).
- **My scoring formula was wrong — did I lose results?** No. The optimizer rescores on load and replays decisions; on divergence, fork. See [`scoring-and-memory.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md).
- **What if it stalls?** Stall and failure are different triggers. Failures route back to the proposing layer ([self-healing](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md)); stalls escalate L1 → L2 → L3 ([the-loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md)). Stuck for other reasons: [troubleshooting](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/05-troubleshooting.md).
- **Why not search at request time instead?** PromptPotter finds a configuration once; from then on it is an ordinary call with no search in the path. [Test-time compute, moved out of the request.](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md#test-time-compute-moved-out-of-the-request)
- **Is this a model router?** No. Live model selection per question is a gateway ([OpenRouter](https://openrouter.ai), [OmniRoute](https://github.com/diegosouzapw/OmniRoute)). PromptPotter tunes the thing a gateway ends up calling, so the two compose.

</details>

## Benchmarks

[![PromptWizard](https://img.shields.io/badge/inspired_by-PromptWizard-blue)](https://arxiv.org/abs/2405.18369)
[![BBEH](https://img.shields.io/badge/benchmark-BBEH-purple)](https://github.com/google-deepmind/bbeh)
[![DSPy](https://img.shields.io/badge/compared_against-DSPy-green)](https://github.com/stanfordnlp/dspy)
[![CAPO](https://img.shields.io/badge/compared_against-CAPO-orange)](https://arxiv.org/abs/2504.16005)

**No head-to-head number is published yet, on purpose.** The comparison is against DSPy's optimizers (**GEPA**, MIPROv2, BootstrapFewShot) and CAPO on *BIG-Bench Extra Hard (BBEH)*, held to a standard this literature mostly does not hold itself to: every method scored on the same held-out rows, one published split seed, one export schema, no cross-paper number mixing. **Numbers publish once** the target model and the optimization budget are held constant across every method too — the budget in calls, reported beside tokens, dollars (priced at run date) and wall clock. Harness: [`bbeh-comparison/`](https://github.com/PromptPotter/prompt-potter-optimizer/tree/main/docs/research/bbeh-comparison/) · what we measure on, what we refuse to, and why: [`benchmarks.md`](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md).

## Limitations

- **Parameter-based optimization only.** It optimizes any pipeline that exposes tunable parameters — prompts, thresholds, model settings — but not model weights, neural architectures, or modality-specific representations.
- **Requires a labeled dataset.** Input/output pairs are mandatory.
- **In-campaign figures are not held-out figures.** The winner's own number is read off the rows that selected it, so it overstates deployment performance. A selection-clean partition is [on the roadmap](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md); published head-to-head numbers use a reserved split instead.

## Documentation

| 🧠 Concepts | ⚙ Operations | 🔬 Methods & research | 🔧 Contributing |
|---|---|---|---|
| [Three-layer loop](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/the-loop.md) | [Install & env](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/manual/02-install.md) | [Related work](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/related-work.md) | [Developer guide](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/README.md) |
| [Scoring and memory](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/scoring-and-memory.md) | [Persistence, state, recovery](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/persistence-and-state.md) | [Benchmarks + metrics](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/research/benchmarks.md) | [Architecture](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/architecture.md) |
| [Structured output](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/concepts/structured-output.md) | [Observability](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/observability.md) | [Verdict resolution](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/verdict-resolution.md) | [Conventions](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/conventions.md) |
| [Nodes and pipelines](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/node-standard.md) | [Backend integration](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/operations/backend-integration.md) | [Candidate elimination](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/methods/candidate-elimination.md) | [Self-healing internals](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/self-healing-internals.md) |

[Roadmap](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/specs/roadmap.md) · [docs index](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/README.md) · [stable API](https://github.com/PromptPotter/prompt-potter-optimizer/blob/main/docs/developer/stable-api.md)

## Citation

```bibtex
@software{promptpotter,
  title  = {PromptPotter: LLM-Driven Evolution of Prompts and Pipelines},
  author = {Streuli, David},
  year   = {2026},
  url    = {https://github.com/PromptPotter/prompt-potter-optimizer}
}
```
