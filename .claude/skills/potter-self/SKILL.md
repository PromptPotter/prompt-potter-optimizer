---
name: potter-self
description: PromptPotter L4 self-optimization — Claude and the operator read a round's artifacts, diagnose why candidates underperformed, and decide what evidence to buy before another paid round. Use at the round-1 gate or a halted loop; on L4, `promptpotter-self`, L1 stall, mode collapse, flat stratification, near-duplicate candidates, candidates ignoring the critique or task_context, l1_generate parse failures, tuning `l1_generate/1` in potter's pipeline.yaml; on opening a `round_NNNN.json`, "why did L1 do that?", "improve the optimizer prompt"; when weighing the next experiment (panel width, seed count, cells, is a result real). Also AUTHORED MODE ("you are the proposer", "you write the arms", "hand-authored round") and the tuning thread ("continue the tuning", "work the board", "next card", "get the price down"). Fires even when "L4" is not said.
---

# L4: collaborative review of L1-generate

**Read → classify → edit → predict → re-run one round → compare, one edit per pass.** This file is
procedure; what is TRUE of L4 is [`l4.md`](../../../docs/concepts/l4.md). L4 edits
`promptpotter/assets/optimizers/potter/pipeline.yaml → resolved_prompts["l1_generate/1"]` (inner) and the
`*_self_optimizing/1` families (outer): never L2/L3's surfaces, `pipeline_params`, `task.md`, a dataset's
config, `task_context` (no layer writes it: a framing diagnosis stops at "→ operator should revise
task_context to X"), or past rounds' scores (swap `{dataset}/dataset.yaml::campaign.scoring` instead).

**The tuning thread** ("continue"): the board is [`roadmap.md`](../../../docs/specs/roadmap.md) § Optimizer tuning — the
standing board; findings are rows of [`benchmarks.md`](../../../docs/research/benchmarks.md) § Every dataset we measured,
written the turn they land. Name the card you take. Thinking is free and a test is paid: exhaust what is
banked, name the result that would change the next decision, and check the instrument can resolve it
(`evidence --subject`) before buying.

**Never name a panel from memory**: the citable set is `citable_fields` (`dispatch/injections/registry.py`).

## Where the evidence is

`{cycle_dir}` is `.promptpotter/projects/{tenant}/campaigns/{campaign_id}/cycles/{cycle_id}/`; an L4 inner
cell lives under the flat `.promptpotter/.inner/{key}/…`. `{cycle_dir}/rounds/round_NNNN.json` holds
candidates, scores and critique; `{cycle_dir}/.runtime/ledger.jsonl` is **the only place the rendered
optimizer prompt survives** (`llm_call` records' `template_fields` + `variables`; `llm_call_start` carries
`injection_dropped`; `race_standing` shows stratification or collapse). Node I/O per round:
`GET …/rounds/{n}/audit`. A replayed round runs no proposer: its walk is `round_proposed`'s `walked`.

## Live-run supervision

**Run `new promptpotter-self/panel` ONCE, in the FOREGROUND, to completion**, from a checkout. A kill
orphans the open inner run (`producer_vanished`); a relaunch continues the same inner campaign, so it costs
the interrupted round, not the cell. The outer `cycle_id` hashes optimizer prompts + benchmark: **iterate
with `resume`**, `new` only after a prompt/config change.

Each self-firing wake reads the newest outer round's audit plus the ledger's rendered input (a log tail is
not supervision):

1. **`l1_generate`**: panels populated (a dropped `L1_MANDATORY` panel is stop-and-diagnose)? Outer:
   `inner_narratives` per seed? Variants: `evidence_grounding.field` in the enum; citations quote text
   that EXISTS in the panel they NAME; hypotheses distinct; no hallucinated node/param.
2. **`l1_critique`**: `priority_fix` / `failure_highlights` quote CONCRETE evidence and name a steer the
   generator may make, never the benchmark's vocabulary or labels.
3. **Scoring**: per-arm `folds`, `election.fit` (θ, θ_se), the matched-parent comparison (never the
   cross-subset round-0 origin), PoBB `p_best` off 0.5, `decisions` cutting the right arm.
4. **`l2_context` / `l3_plan` when fired**; **≥1 inner campaign per outer sample batch**, same reads.

**Stop-and-diagnose:** `raw_chars: 0` / no candidates · an outer sample back in ~0.0s (stale cache) ·
off-enum grounding · an optimizer call > 2 min · a headline Δ disagreeing with `vs_reference` / `improved`.
A quiet outer round is normal (the ledger heartbeats "inner rX/Y · best Z%"). Hang triage:
[`persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run.
**Default the fix to the prompts**; code only on a structural cause the data shows.

**Freeze the inside, iterate the outside.** An edit inside the inner-origin fingerprint voids every banked
outer cell ([`l4.md`](../../../docs/concepts/l4.md) § Invariants): batch such edits, re-measure once. An
outer-family edit trips the resume divergence gate: `new`, never `resume`. Different `inner_origin` values
never replayed each other's cells: count them before trusting a cross-campaign number.

## Reading the outer panel

- **`evidence --subject campaign:<id>` answers variance and power** (`power.cells_for_largest_gap`,
  `order_confound`); run it rather than quoting a figure. Short of cells: add paired cells first; cut to
  one seed only when data shows a small arm×seed interaction.
- **Read the SHAPE**: each cell's per-round matched-parent `improved`; a round 1 that lifts too rarely is
  an `l1_generate` problem.
- Validate a cheap proxy at the ARM level, never per cell. Widen candidates semantically to raise arm SD.
- A cell whose constant-answer floor exceeds its origin is disqualified (`rewards_collapse`). An unchanged
  prompt can take a step (the proxy reads the promoted parent): open the cell's `review.md` before crediting an arm.
- Compare arms on cells BOTH met; a win is a few rows. Price every panel in wall clock *and* dollars.

**The cost ladder.** A decision point is one frozen inner round (less round 0) carrying a parsed
`l1_generate` call; grading a prompt on one costs one call plus a scoring pass (`os.walk`: a glob cannot
see the dot-directory). Run the cheapest unexhausted rung: 0 free (walk `.promptpotter/`) · 1 cents
(single-prompt probes over decision points) · 2 one panel (one challenger vs the pinned baseline) · 3 one
outer round · 4 deferred. **We push the first few rounds, not the long campaign** (operator); the one-step
→ trajectory link is unvalidated until the pilot gives a noise floor and a known-sign effect.

## The playbook

1. **Read** the prompt, the rendered input, candidates and critique, the PoBB stream (all candidates to
   `n_min` with near-identical intervals = flat stratification). A critique disagreeing with the
   composite is a scoring problem, not L1's.
2. **Classify** — the single closest mode; if two apply, the upstream one.

| Mode | Symptom | Edit |
|---|---|---|
| **★ Semantic restatement** (check first) | String-distinct candidates testing one idea, each +0.000; `idea_fingerprint` is word overlap, so judge `changes_description` across rounds yourself. | State the **hypothesis**, differing from every `mutation_memory` row: another panel, node or edit vocabulary. |
| Off-task | Candidates contradict `task_context`. | Quote one phrase of `task_context` in the rationale. |
| Ignoring critique | Round N+1 repeats round N's named failure. | Name the critique bullet each candidate addresses. |
| Surface-only | Identical `pipeline_overlay`, cosmetic text, composite ≈ parent. | Name the axis moved and the effect expected. |
| Params overreach | `param_scope_discipline` fires. | Name the prompt-field evidence exhausted first. |
| Critique-score divergence | Critique says A, composite says B. | Not L1: `campaign.scoring`. |

Parse failures, no-ops and verbatim duplicates were measured absent: no edit there without `l1_yield` below 1.00.

3. **Round-trace checklist**, beyond what `validators.py` and `overlay_failures()` enforce: `runtime_failures`
   empty in round 1 while sibling forks failed is broken inheritance; `l1_config_not_in_runtime_failures`
   catches exact `(param, value)` only (flag a near value); standing constraints (PEAKED axes, param axes
   last, numeric envelopes) read off the prompt; Jaccard ≥ 0.5 over a pair's `changes_description` →
   `intra_round_paraphrase`, a theme word in ≥ N/2 → `theme_mode_collapse`; `pipeline_overlay` keys are
   real `param_keys`. Report violations at the TOP of the reply (`✗ peaked_axis_violation (C1.1: …)` ·
   `⚠ intra_round_paraphrase (C1.2 ↔ C1.4 Jaccard 0.58)`).
4. **Propose, predict, re-run one round**: a unified diff against `l1_generate/1`, a one-line prediction,
   `resume` (`--on-divergence fork` keeps prior runs comparable). Held → lock it in; else reclassify —
   **never reword a failed edit**.

**Edit etiquette.** One edit per pass; no hidden defaults. No new response field from the prompt side
(`L1Variant` is `extra="forbid"`; `targets_rows` is the shipped answer to semantic restatement, not to be
re-prescribed in prose). A new `{{slot}}` is an `@signal` renderer
([`dispatch-hub.md`](../../../docs/concepts/dispatch-hub.md)), never a summary at the prompt site.

## Authored mode — you are the proposer

`--set config.optimization.arm_gate=author`: every round holds for arms you submit (`review`: the generator
drafts, you keep drafts by index and/or add arms); answer with `submit-arms --arms arms.yaml`. An arm edits
the round's parent, one per hypothesis, a combined arm only beside its parts. Before an arm earns a cell:

1. **The lever is in the node's sight**: its `possible` set in `dispatch/layout.py` and its rendered prompt in a banked round.
2. **The lever binds**: an axis the inner solver never reaches, or one the inner
   `{dataset}/dataset.yaml::pipeline` does not open, moves nothing; settle headroom with `ab` first.
3. **The edit changes what the node EMITS**: run it on banked decision points against the parent's output.
