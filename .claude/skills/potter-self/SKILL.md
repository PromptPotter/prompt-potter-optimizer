---
name: potter-self
description: PromptPotter L4 self-optimization — Claude and the operator read a round's artifacts, diagnose why candidates underperformed, and decide what evidence to buy before another paid round. Use at the round-1 gate or a halted loop; on L4, `promptpotter-self`, L1 stall, mode collapse, flat stratification, near-duplicate candidates, candidates ignoring the critique or task_context, l1_generate parse failures, tuning `l1_generate/1` in potter's pipeline.yaml; on opening a `round_NNNN.json`, "why did L1 do that?", "improve the optimizer prompt"; when weighing the next experiment (panel width, seed count, cells, is a result real). Also AUTHORED MODE ("you are the proposer", "you write the arms", "hand-authored round") and the tuning thread ("continue the tuning", "work the board", "next card", "get the price down"). Fires even when "L4" is not said.
---

# L4: collaborative review of L1-generate

The operator (with you as analyst) reads a round's artifacts and edits the `l1_generate` optimizer prompt
before buying another round: **read → classify → edit → predict → re-run one round → compare, one edit per
pass.** This file is procedure; what is TRUE of L4 is [`l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md).

**The tuning thread** ("continue"): [`roadmap.md`](../../../docs/specs/roadmap.md) § Optimizer tuning — the standing board
is the board; findings are rows of [`benchmarks.md`](../../../docs/research/benchmarks.md) § Every dataset we measured.
Name the card you take, work it, write each measured number into benchmarks the turn it lands. Thinking is
free and a test is paid: exhaust what is banked (`.promptpotter/`, `.promptpotter/.inner/`), name the result
that would change the next decision, and check the instrument can resolve it (`evidence --subject`) before buying.

## Mental model

L1 (`l1_generate`) proposes from the panels its live layout renders (`NODE_LAYOUTS`, `dispatch/layout.py`);
L2 moves `l1_layout` / `l1_overrides` on L1 stall; L3 replans on L2 stall. No layer writes `task_context`
(`promptpotter/application/optimizers/potter/CLAUDE.md`). **Never name a panel from memory**: the citable set
is `@signal(..., citable=True)` ∩ the node's live layout (`citable_fields`, `dispatch/injections/registry.py`).

L4 edits `promptpotter/assets/optimizers/potter/pipeline.yaml → resolved_prompts["l1_generate/1"]` (inner) and
the `*_self_optimizing/1` families (outer) — not L2/L3's surfaces, not `pipeline_params`, not `task.md`.

## Artifact map (read in order)

Cycle root: `.promptpotter/projects/{tenant}/campaigns/{campaign_id}/cycles/{cycle_id}/`; active pointer
`.promptpotter/projects/{tenant}/.workspace/active_session.json`. An L4 inner cell lives under the flat `.promptpotter/.inner/{key}/…`.

| Step | File | What you extract |
|---|---|---|
| 1 | `pipeline.yaml` → `resolved_prompts["l1_generate/1"]` | The template; cross-check its `{{slots}}` against `injection_table` |
| 2 | `{cycle_dir}/rounds/round_NNNN.json` | Parsed candidates, scores, `overlap`, critique, each candidate's target `prompts`. No rendered prompt |
| 3 | `{cycle_dir}/.runtime/streams/round_NNNN_pobb.jsonl` | Did variants stratify or collapse; who was cut first |
| 4 | `{cycle_dir}/.runtime/ledger.jsonl` | Escalations, decisions, spend. **The only place the rendered optimizer prompt survives**: each `record_type: "llm_call"` record's `template_fields` + `variables`; `llm_call_start` carries `injection_dropped` |
| 5 | `{cycle_dir}/dashboard.json` | Composite trajectory + recent rules |

A replayed round runs no proposer: its walk is the `round_proposed` record's `walked` state.

## Live-run supervision

**Run `new promptpotter-self/panel` ONCE, in the FOREGROUND, to completion**, from a checkout (the panel is the research tier's, [`research/datasets/CLAUDE.md`](../../../research/datasets/CLAUDE.md)). A kill orphans the open inner run
(`producer_vanished`, not cached); a relaunch continues the same inner campaign
(`runner/inner/spawn.py::inner_campaign_id`), so a kill costs the interrupted round, not the cell. The outer
`cycle_id` hashes optimizer prompts + benchmark, so re-`new` with unchanged prompts collides on ONE cycle.
**Iterate with `resume`**; `new` only after a prompt/config change.

**The cadence is self-firing and the interval is work**: each wake reads ALL of the below from the newest
outer `.runtime/cache/rounds/round_NNNN.json` plus the ledger's rendered input. A log tail is not supervision.

1. **`l1_generate`** — panels populated (`injection_dropped`; a name there that is also `L1_MANDATORY` is
   stop-and-diagnose)? Outer: `inner_narratives` present per seed? Variants: `evidence_grounding.field` in the
   enum; citations quoting text that EXISTS, from the panel they NAME; hypotheses distinct; no hallucinated node/param.
2. **`l1_critique`** — inner reads sample transcripts, outer reads inner run narratives
   (`panels.py::_inner_narrated`). `priority_fix` / `failure_highlights` quote CONCRETE evidence and name a
   steer the generator may make, never the benchmark's vocabulary or labels.
3. **Scoring** — per-arm `folds`, `election.fit` (θ, θ_se), the matched-parent comparison (never the
   cross-subset round-0 origin), the PoBB stream (`p_best` off 0.5?), `decisions` (cuts on the right arm?).
4. **`l2_context` / `l3_plan` when fired** — `validators/l2_behavior.py`, `l3_output.py`.
5. **≥1 inner campaign per outer sample batch** — the same reads under `.inner/<key>/…/campaigns/`.

**Stop-and-diagnose:** `raw_chars: 0` / an empty candidate list · an outer sample back in ~0.0s (stale
cache) · off-enum grounding fields · any optimizer call > 2 min · a headline Δ disagreeing with
`vs_reference` / `improved`. A quiet outer round is normal (the ledger heartbeats "inner rX/Y · best Z%").
Hang triage: [`persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run.
**Default the fix to the prompts**; reach for code only on a structural cause the data shows.

## Freeze the inside, iterate the outside

An edit inside the inner-origin fingerprint voids every banked outer cell; its membership and preflight
are [`l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md) § Invariants — break one and the corpus is void, silently.
Batch such edits and re-measure once. An outer-family edit costs nothing banked but trips the resume
divergence gate: `new`, never `resume`. Count the fingerprints before trusting a cross-campaign number —
different `inner_origin` values never replayed each other's cells.

## Reading the outer panel

- **`promptpotter evidence --subject campaign:<id>` answers variance and power** (`variance.*`,
  `power.{paired_se,min_detectable_effect,cells_for_largest_gap}`, `replicates`, `order_confound`); run it
  rather than quoting a figure. Fewer cells than `cells_for_largest_gap`: adding paired cells is the
  cheapest move; cut to one seed only when data shows a small arm×seed interaction.
- **Read the SHAPE**: each cell records a per-round matched-parent `improved` verdict
  (`runner/inner/spawn.py::_lift_shape`); a round 1 that lifts too rarely is an `l1_generate` problem.
- **Validate a cheap proxy at the ARM level, never per cell.** **Widen candidates semantically** — another
  evidence framing, node or edit vocabulary raises arm SD for free.
- **A cell whose constant-answer floor exceeds its origin is disqualified**
  (`application/diagnostics/seed_screen.py::rewards_collapse`). **An unchanged prompt can take a step**
  (`domain/l4/proxies.py::compute_outer_proxies` reads the promoted parent): open the cell's `review.md`
  before crediting an arm.
- Compare arms on the cells BOTH met; a win is a few rows. Price every panel proposal in wall clock *and*
  dollars (`<one-budget>`).

## The cost ladder

A **decision point** is one inner round frozen in `rounds/round_NNNN.json`; grading an optimizer prompt on
one costs one `l1_generate` call plus a scoring pass. The bank is the inner rounds under
`.promptpotter/.inner/` carrying a parsed `l1_generate` call, less round 0 (`os.walk`; a glob cannot see a
dot-directory). **Always run the cheapest unexhausted rung**: 0 free (walking `.promptpotter/`) · 1 cents
(single-prompt probes over decision points) · 2 one panel (one challenger against the pinned baseline) ·
3 one outer round · 4 deferred (wider panels, longer campaigns, a new benchmark). **We are not optimizing the long campaign** (operator): push the first few rounds. The one-step → trajectory link is unvalidated: the pilot gives the noise floor and a known-sign effect first.

## The playbook

1. **Read**: the prompt; the audit trail (render what the LLM saw from the ledger's `llm_call` record;
   candidates, scores and the `l1_critique` block from `round_NNNN.json`); the PoBB stream (all candidates to
   `n_min` with near-identical intervals = flat stratification; one running away early: read why); the
   critique (one disagreeing with the composite is a scoring problem, not an L1 problem).
2. **Classify the failure mode** — the single closest; if two apply, the upstream one.

| Mode | Symptom | Edit |
|---|---|---|
| **★ Semantic restatement** (check first) | String-distinct candidates all testing one idea, each at +0.000; `idea_fingerprint` is word overlap, so judge the `changes_description` texts across rounds yourself. | State the **hypothesis** under test, differing from every `mutation_memory` row: another panel, node or edit vocabulary. |
| Off-task | Candidates contradict the frozen `task_context`. | Quote one phrase of `task_context` in the rationale. |
| Ignoring critique | Round N+1 repeats the failure round N's critique named. | Name the critique bullet each candidate addresses. |
| Surface-only | Identical `pipeline_overlay`, cosmetic text change, composite ≈ parent. | Name the axis moved and the effect expected (`mutation_memory` / `axis_memory`). |
| Params overreach | `param_scope_discipline` (`validators/l1_behavior.py`) fires. | Name the prompt-field evidence exhausted first. |
| Critique-score divergence | Critique says A, composite says B. | Not L1: `{dataset}/dataset.yaml::campaign.scoring`. |

Parse failures, no-ops and verbatim duplicates were measured absent; spend no edit there without fresh
evidence (`l1_yield` below 1.00).

3. **Walk the round-trace checklist.** Code enforces `validators/l1_strict.py` and `overlay_failures()`
   (`application/bench/children.py`); for the rest your analysis IS the gate: `runtime_failures` empty in
   round 1 while sibling forks failed is broken inheritance (`bench/cycle.py::Cycle.runtime_failures`);
   `l1_config_not_in_runtime_failures` catches exact `(param, value)` matches only — flag a value near a
   failing one; standing constraints (PEAKED axes, param axes last, numeric envelopes) read off the prompt;
   Jaccard ≥ 0.5 over a pair's `changes_description` → `intra_round_paraphrase`, a theme word in ≥ N/2 →
   `theme_mode_collapse`; `pipeline_overlay` keys are real `param_keys`. Report violations as a checklist at
   the TOP of the reply (`✗ peaked_axis_violation (C1.1: …)` · `⚠ intra_round_paraphrase (C1.2 ↔ C1.4
   Jaccard 0.58)`), before any narrative.
4. **Propose, predict, re-run one round**: a unified diff against `resolved_prompts["l1_generate/1"]`, a
   one-line prediction, then `resume` (`new` after a prompt/config change). Prediction held → lock it in;
   else reclassify — **never reword a failed edit**.

## Authored mode — you are the proposer

`--set arm_gate=author`: every round holds and the arms are the ones you submit (`arm_gate: review`: the
generator drafts, you keep drafts by index and/or add arms). Read the hold in the readout, answer with
`submit-arms --arms arms.yaml`. An arm edits the round's parent (`pipeline_overlay: {l1_critique: {instruction: …}}`):
one per hypothesis, a combined arm only beside its parts. Three checks before an arm earns a cell, in order:

1. **The lever is in the node's sight** — its `possible` set in `dispatch/layout.py` and its rendered prompt in a banked round.
2. **The lever binds in the inner benchmark** — a budget axis the solver never reaches, or one the inner
   `{dataset}/dataset.yaml::pipeline` does not open, moves nothing; settle headroom with `ab` first.
3. **The edit changes what the node EMITS** — run it on banked decision points against the parent's output.

## Edit etiquette

- **One edit per pass.** No hidden defaults; trim to invariants, not history.
- **No new response-contract field from the prompt side.** `L1Variant` is `extra="forbid"`
  (`dispatch/schemas.py::L1Variant`); `targets_rows` is the shipped STRUCTURAL answer to semantic
  restatement — do not re-prescribe it in prose. Prefer `changes_description`.
- **A new `{{slot}}` is an `@signal` renderer under `dispatch/injections/`**
  ([`dispatch-hub.md`](../../../docs/developer/dispatch-hub.md)); never summarize a field at the prompt site.
- **A framing diagnosis stops at "→ operator should revise task_context to X"** — no layer writes it.
- An optimizer prompt edit changes `JobSearchPoint.content_hash` for the next round, not the target
  cycle's origin hash; suggest `--fork-on-divergence` to keep prior runs comparable.
- **L4 never** generates candidates (L1's), refines `task_context`, replans (L3's), modifies `task.md` or
  per-dataset configs, or re-scores past rounds — swap the formula in
  `{dataset}/dataset.yaml::campaign.scoring` and let the next round recompile.
