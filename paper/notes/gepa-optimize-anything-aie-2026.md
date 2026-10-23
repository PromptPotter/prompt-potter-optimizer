# GEPA `optimize_anything` at AI Engineer World's Fair — what it claims, what it is measured against

Read 2026-10-19. An observation for the related-work and results sections, not a result of ours.

**Why it matters here.** GEPA is the reference peer ([`../../docs/research/landscape.md`](../../docs/research/landscape.md)
§ DSPy — GEPA, MIPROv2, BootstrapFewShot; shipped inside DSPy and MLflow). Its author presented
`optimize_anything` on the main stage of the AI Engineer World's Fair, so these numbers are the ones a
reviewer will have seen. Two consequences for M14: the **scope line** in
[`../../docs/research/related-work.md`](../../docs/research/related-work.md) § Against GEPA and AlphaEvolve
is under pressure, and the **ARC-AGI headline** is the cleanest public example of the
unoptimized-origin problem the citation filter already names (*Optimization before Evaluation*,
[2604.27637](https://arxiv.org/abs/2604.27637), [`../README.md`](../README.md) § The citation filter).

## Sources (all read, not recalled)

- Talk: Lakshya A. Agrawal, *Beating RL With Reflection: GEPA and Optimize Anything*,
  https://www.youtube.com/watch?v=OA-Mc60Rboo — official page with corrected transcript:
  https://ai.engineer/talks/OA-Mc60Rboo-youtube-c7b7a577aa52a7d42876
- Blog: https://gepa-ai.github.io/gepa/blog/2026/02/18/introducing-optimize-anything/ (source:
  `gepa-ai/gepa` `docs/docs/blog/posts/2026-02-18-introducing-optimize-anything/index.md`)
- ARC-AGI example code: https://github.com/gepa-ai/gepa/tree/main/examples/arc_agi (`main.py`, `utils.py`)
- Unicorn notebook: https://github.com/gepa-ai/gepa/blob/main/docs/docs/tutorials/3d_unicorn_optimization.ipynb
- ARC Prize leaderboard data, `generatedAt 2026-10-15`: `https://arcprize.org/media/data/leaderboard/v1.json`
  (`v2.json`, `v3.json` likewise), rendered at https://arcprize.org/leaderboard

## 1. Scope: "any text artifact you can score"

`optimize_anything(seed_candidate, evaluator, dataset, valset, objective, background, config)`. The
evaluator returns `(score, side_info)`; *actionable side information* (ASI) is an open dict — compiler
errors, traces, **rendered images** — fed to the reflection LM. Three modes, from the blog:

| Mode | Data | Example |
|---|---|---|
| Single-task search | none; the candidate is the solution | one CUDA kernel |
| Multi-task search | related problems, cross-transfer | pelican SVG and 3D unicorn: the "dataset" is the **rubric aspects**, one evaluator call per aspect, Pareto over aspects |
| Generalization | `dataset` + held-out `valset` | ARC-AGI agent, prompt optimization |

**Pressure on our scope line.** related-work.md says *"GEPA evolves the instruction text of a DSPy
program … PromptPotter evolves prompt and pipeline jointly — that is scope."* `optimize_anything`
evolves a whole agent file (code, sub-agents, control flow, prompts) and accepts a dict of named text
parameters. The 🟡 on *Tunes the whole pipeline* must be re-read against it before the capability
table is quoted in the manuscript. What still separates us is the statistical layer (PoBB, the
subset-invariant ruler, priced measurements), not the reach of the search. Same conclusion the
harness-evolution grouping reached ([`../related/related-work.md`](../related/related-work.md) § Agent-harness evolution).

## 2. The ARC-AGI headline: 32.5% → 89.5%

**What it is, from the code.** ARC-AGI-**1** (blog: *"Here, we tackle ARC-AGI1"*), HuggingFace
`dataartist/arc-agi`: train 200 / val 200 from the `training` split, test = the 400-task public
`evaluation` split. Model `openrouter/google/gemini-3-flash-preview`, both inside the agent and as
the reflection LM. Rules in `BACKGROUND`: ≤ 10 LLM calls and ~$0.8–1.0 per task, 2 attempts per test
input; score penalised −0.1 above $1. Seed = a 10-line single-call agent. Result: test 32.5% → 89.5%,
val 56.5% → 93.5%, after 16 reflection rounds (talk), optimized agent "twice the cost per task" (blog).

**What it is measured against: nothing but its own seed.** ARC Prize, ARC-AGI-1 Semi-Private, same
date window:

| Model (reasoning setting) | Score | $/task |
|---|---|---|
| Claude Opus 5.5 (High) · Claude Fable 5 · GPT-6.1 Sol (High) · Gemini 3.8 Flash (High) · Gemini 4 Argon | 98.5% | 0.06–0.21 |
| Human panel | 98.0% | 17 |
| **Gemini 3 Flash Preview (High)** — the demo's model, no harness | **84.7%** | 0.17 |
| Gemini 3 Flash Preview (Medium) | 57.7% | 0.06 |
| Gemini 3 Flash Preview (Low) | 29.0% | 0.02 |

Reading, with the caveat stated each time it is used: **the splits differ** (GEPA the public evaluation set, ARC
Prize semi-private), and the public set has been online since 2019, so contamination is possible and
untested. Under that caveat:

- The seed's 32.5% sits at Flash's **Low** reasoning row. Most of the +57 points is the distance a
  reasoning-effort setting closes for $0.17/task. The headline never prices the lift against that dial.
- ARC-AGI-1 is **saturated**: frontier models score above the human panel for cents. No harness can
  show a lift on a frontier model there.
- **This is the paper's argument handed to us.** Every lift is quoted against the cheapest competing
  knob — reasoning effort, a model swap — on a priced axis. That is what *Every measurement is priced*
  and cost-per-fitness exist for; this example makes the case in one table.

**ARC-AGI-2 and -3, where there is headroom** (ARC Prize, same data):

| | Top model | Gemini 3 Flash (High) |
|---|---|---|
| ARC-AGI-2 | 95.0% GPT-6 Astra (Max); Opus 5.5 (High) 93.3% | 33.6% |
| ARC-AGI-3 | 62.7% GPT-6 Astra (Max) | — |

ARC-AGI-3 also lists "GPT-6 Astra – Provider Adapter" entries at 98–99.9%. **Unverified what
"Provider Adapter" means**; if it is a provider-built harness, it is direct evidence that harnesses
still move the absolute number where the benchmark is unsaturated. Read ARC Prize's methodology
before quoting it.

## 3. Experiments this asks for

Ordered by cost. Each follows [`../../docs/research/evidence-to-publish.md`](../../docs/research/evidence-to-publish.md)
§ What a head-to-head needs: one model stamped from the wire, the same held-out rows, budget matched
in spent tokens.

1. **Price the lift against the dial (cheap, decisive).** 2 × 2 on a 50-task slice of ARC-AGI-1
   public evaluation set: {seed agent, GEPA's published best agent} × {Flash Low, Flash High}. If seed@High ≈
   evolved@Low, the headline is a reasoning-effort result. Uses GEPA's own code, no optimization run.
2. **Newer, cheaper model, where there is room.** Cheap rows with headroom on ARC-AGI-1 today:
   Gemini 3.5 Flash-Lite (Low 17.0% / High 53.5%, $0.01–0.09), DeepSeek V4.1 Flash (None 7.8% / Max
   94.5%). The question is whether an evolved harness on the Low/None setting beats the same model's
   own High/Max for less money. Only that comparison is worth a sentence.
3. **Head-to-head on an unsaturated target.** potter vs `optimize_anything` evolving the same agent
   harness, same model, on ARC-AGI-2 (or -3 if an episode can ride `harbor`; ARC-AGI-3 is interactive
   and needs a new evaluator). This is where the bench thesis is tested on GEPA's own showcase.

Run 1 is ~200 agent episodes at ≤ $1 → ≤ $200 worst case, far less at Low. Cap it.

## 4. The unicorn, and what we take from it

From the notebook: `seed_candidate=None`, objective *"Optimize a Python program (build123d + pyrender)
to generate a 3D unicorn"*, 4 aspects (`overall`, `anatomy`, `mesh_quality`, `visual_appeal`, each
`SCORE: X/100`), evaluator runs the candidate with `subprocess.run(timeout=120)`, sends 3 rendered
views to the VLM; **`MODEL = bedrock/us.anthropic.claude-opus-4-6-v1` is proposer and judge alike**,
`max_metric_calls=200`. So "Opus 4.6 vs optimize_anything" is Opus one-shot vs Opus in a loop graded
by itself — a fair self-comparison, and a self-grading one. No example dataset exists or is needed;
the pelican SVG demo is the same shape (6 aspects, Gemini 3 Flash VLM, 150 calls).

**Why we care: representation change.** The image is not optimized; the program that renders it is.
That is the umbrella's invariant (behaviour steered through a text artifact, model frozen) made
visible to a non-technical reader in one picture. Roadmap entry: [`../../docs/specs/roadmap.md`](../../docs/specs/roadmap.md)
§ Captured — pending triage, *Render-and-judge demo*.

## 5. Claimed in the talk, not checked

From the talk description, unverified here: GEPA after one reflection on 3 examples doubles GRPO's
gain after 25,000 rollouts; AMD NPU coding agent 4% → 30%; GPT-5 mini on Go issues 24% → 93% with
learned skills that transfer to Claude Sonnet; Databricks tuning an open model past Claude Opus at
90× lower cost; GPT-4.1 mini beating GPT-4.1. Each needs its own source before it is cited.

## ⚠️ Do not follow the slide's short link

The slide after the ARC-AGI result (13:42, https://youtu.be/OA-Mc60Rboo?t=822) shows a QR code and
`tinyurl.com/gepa-agent-search`. Typed on 2026-10-19 it redirected tinyurl → `zikaf.com` →
`bafledbirdlink.com`, a fake McAfee scareware page. Whether the alias was hijacked or never GEPA's is
unknown. The code is on GitHub (links above); never use the short link or the QR code.
