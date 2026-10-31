---
name: potter-self
description: PromptPotter L4 self-optimization — Claude and the operator read a round's artifacts, diagnose why candidates underperformed, and decide what evidence to buy before another paid round. Use at the round-1 gate or a halted loop; on L4, `promptpotter-self`, L1 stall, mode collapse, flat stratification, near-duplicate candidates, candidates ignoring the critique or task_context, l1_generate parse failures, tuning `l1_generate/1` in potter's pipeline.yaml; on opening a `round_NNNN.json`, "why did L1 do that?", "improve the optimizer prompt"; when weighing the next experiment (panel width, seed count, cells, is a result real). Also AUTHORED MODE ("you are the proposer", "you write the arms", "hand-authored round") and the tuning thread ("continue the tuning", "work the board", "next card", "get the price down"). Fires even when "L4" is not said.
---

# L4: collaborative review of L1-generate

L4 is **not another LLM layer**: it is the operator (with you as analyst) reading a round's artifacts
and editing the `l1_generate` optimizer prompt before buying another round. The loop: **read →
classify → edit → predict → re-run one round → compare**, **one edit per pass**.

**This file is procedure. What is TRUE of L4 is [`docs/specs/l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md); what a corpus measured, ask `promptpotter evidence`.** When the operator proposes something else, price both on the cost ladder and say which wins; prefer the answer already on disk.

## The tuning thread — start here when the operator says "continue"

**[`docs/specs/roadmap.md`](../../../docs/specs/roadmap.md) § Optimizer tuning is the board; its findings are rows of [`docs/research/benchmarks.md`](../../../docs/research/benchmarks.md) § Every dataset we measured.** Read the board first, name the card you take, work it, and write each measured number into benchmarks the turn it lands. The operator steers the order.

**Thinking is free and a test is paid, so the order is fixed.** Exhaust what is banked first: walk `.promptpotter/` and `.promptpotter/.inner/`, re-read rounds already paid for, and state what the test could show that they cannot. Then name the result that would change the next decision and check the instrument can resolve it (`evidence --subject`); a test that cannot separate its arms is not bought. **Count on disk, never recall** — the spec's figures carry their corpus and date.

## Mental model

Three nested loops (`promptpotter/application/optimizers/potter/CLAUDE.md`):

- **L1** (`l1_generate`) proposes candidates from the panels its live layout renders (`NODE_LAYOUTS["l1_generate"].floor`, `optimizers/potter/dispatch/layout.py`), under L3's `plan` and the operator's frozen `task_context`.
- **L2** (`l2_context`) fires on L1 stall and moves `l1_layout` (panels) and `l1_overrides` (exploration). **It cannot write `task_context`** (`L2ContextOutput` has no such field).
- **L3** (`l3_plan`) replans on L2 stall, writing `L3Memory.plan`.

**Never name a panel from memory.** The citable set is `@signal(..., citable=True)` ∩ the node's live layout (`citable_fields`, `dispatch/injections/registry.py`); a panel that does not render invites a fabricated citation.

L4 edits `promptpotter/assets/optimizers/potter/pipeline.yaml → resolved_prompts["l1_generate/1"]` — not L2/L3's surfaces, not `pipeline_params`, not `task.md`.

## Artifact map (what to read, in order)

Cycle root: `.promptpotter/projects/{tenant}/campaigns/{campaign_id}/cycles/{cycle_id}/`; active pointer `.promptpotter/projects/{tenant}/.workspace/active_session.json`. An L4 inner cell lives under the flat `.promptpotter/.inner/{key}/…`, never nested under its outer cycle.

| Step | File | What you extract |
|---|---|---|
| 1 | `promptpotter/assets/optimizers/potter/pipeline.yaml` → `resolved_prompts["l1_generate/1"]` (outer: the `*_self_optimizing/1` families beside it, picked by `promptpotter-self`'s `optimization.nodes`) | The template you will edit |
| 2 | `{cycle_dir}/rounds/round_NNNN.json` | Parsed candidates, scores, `overlap` (its `advance`), critique, each candidate's `prompts` (the target, read-only). **No rendered prompt** |
| 3 | `{cycle_dir}/.runtime/streams/round_NNNN_pobb.jsonl` | Did variants stratify or collapse; who was cut first |
| 4 | `{cycle_dir}/.runtime/ledger.jsonl` | Escalations, decisions, spend. **The ONLY place the rendered optimizer prompt survives**: each `payload_kind: "llm_call"` record's `template_fields` + `variables`; the `llm_call_start` beside it carries `prompt_chars`, `injection_chars`, `injection_dropped` |
| 5 | `{cycle_dir}/dashboard.json` | Composite trajectory + recent rules |

On a replayed round the round cache's `l1_generate` entry is a `payload_kind: "synthesized"` stub — useless as evidence. The read VERBS are root `CLAUDE.md` § Commands'.

## Live-run supervision

**Behaving well is found empirically:** run, read what the loop produced, fix the ROOT, re-run. Several restarts is the loop, not a failure.

**Run `new promptpotter-self` ONCE, in the FOREGROUND, to completion.** A kill orphans the open inner run (`producer_vanished`, `FAILED`, not cached); a relaunch continues the *same* inner campaign (`runner/inner/spawn.py::inner_campaign_id`), so a kill costs the interrupted round, not the cell. The outer `cycle_id` hashes optimizer prompts + benchmark, so re-`new` with unchanged prompts collides on ONE `cycle_id` and sandbox — "N campaigns, identical stats" is one measurement shown N times. **Iterate with `resume`**; `new` only after a prompt/config change.

**The cadence is SELF-FIRING and the interval is WORK.** Schedule wake-ups the moment a run starts; each wake is a full pass over the list below, and between ticks keep investigating the fresh files. **A passive log Monitor is not supervision** — every real bug so far (estimator inconsistency, evidence starvation, proxy annihilation) came from reading measurement files. The operator is the developer/user; you own everything else.

**Default the fix to the prompts** (`resolved_prompts`: base families inner, `*_self_optimizing/1` outer). Reach for code ONLY on a structural cause the data shows (broken information flow, a missing analysis, a wiring gap) — name it first.

### The per-checkup reading list — every tick reads ALL of these, not just the log tail

Open the newest outer `{cycle_dir}/.runtime/cache/rounds/round_NNNN.json` for each tier's output and take the rendered INPUT from the ledger (row 4):

1. **`l1_generate`** — panels populated? `injection_dropped` answers it; **a name there that is also `L1_MANDATORY` is stop-and-diagnose**. For the OUTER generator: `inner_narratives` present with a story per seed (the evidence an edit must ground on)? Then the variants: `evidence_grounding.field` in the enum; citations quoting text that EXISTS in the input, from the panel they NAME; hypotheses distinct; `changes_description` reporting the override beside it; no hallucinated node/param.
2. **`l1_critique`** — inner reads SAMPLE TRANSCRIPTS + MODEL REASONING, outer reads INNER RUN NARRATIVES, a matched pair each silent where the other fires (`panels.py::_inner_narrated`). `priority_fix` / `failure_highlights` quote CONCRETE evidence, and `priority_fix` names a steer the generator may make — the inner optimizer's job, never the benchmark's vocabulary or labels.
3. **Scoring** — per-arm `folds` (accuracy, `mean_fitness_ci_lo`), `election.fit` (θ, θ_se), the **matched-parent** comparison (never the cross-subset round-0 origin), the PoBB stream (`p_best` off 0.5?), `decisions` (cuts on the right arm?).
4. **`l2_context` / `l3_plan` when fired** — their checks (`validators/l2_behavior.py`, `l3_output.py`); is the move evidence-anchored, the plan sane and in its render cap?
5. **≥1 inner campaign per outer sample batch** — the same reads under `.inner/<key>/…/campaigns/`.

**STOP-AND-DIAGNOSE:** `raw_chars: 0` / an empty candidate list · an outer sample back in ~0.0s (stale cache) · off-enum grounding fields · any optimizer call > 2 min · a headline Δ disagreeing with `vs_reference` / `improved`.

A quiet outer round is normal (awaiting an inner campaign; the ledger heartbeats "inner rX/Y · best Z%"). Hang triage: [`docs/operations/persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run.

## Freeze the inside, iterate the outside

An edit inside the inner-origin fingerprint voids every banked outer cell.

- **`application/runner/inner/connector.py::_identity_config` is the fingerprint's membership — read it, never recall it.** An edit there is a corpus reset: batch those and re-measure once. An edited `promptpotter-self/panel/dataset.yaml::panel` seat voids only itself.
- **The `*_self_optimizing` families in `promptpotter/assets/optimizers/potter/pipeline.yaml` are the L4 edit surface and cost nothing banked.** An edit still trips the RESUME divergence gate: `new`, never `resume`; the archive stays.
- **Count the fingerprints before trusting a cross-campaign number.** Different `inner_origin` values never replayed each other's cells, so their spread is the instrument moving. Run init's preflight states how many prior campaigns a novel instrument matches (`application/preflight.py::_check_instrument_replays`, an INIT warning in `readout.log`).

## What the outer panel can and cannot tell you

> **`promptpotter evidence --subject campaign:<id>` answers variance and power** — `variance.*`, `power.{paired_se,min_detectable_effect,cells_for_largest_gap}`, `replicates`, `order_confound`. Run it rather than quoting a figure.

- **Pairing makes the comparison possible** (seed variance is several times arm variance). Where the panel runs fewer cells than `power.cells_for_largest_gap`, adding paired cells is the cheapest move.
- **Read the SHAPE, legible where the scalar is not**: each cell records a per-round within-round matched-parent `improved` verdict (`application/runner/inner/spawn.py::_lift_shape`). Target: most cells lift in round 1, about half again in round 2, stragglers in round 3. A round 1 that lifts too rarely is an `l1_generate` problem — diagnose it from candidates on disk.
- **The scored term is `mean_bench_lift`** — held-out accuracy of the adopted prompt minus the origin's, averaged over the round budget — rewarding lift **early and general**; a promotion that held only on its selection rows scores zero. `mean_round_delta` is served beside it and scores nothing. Why: [`docs/specs/l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md) § The measurand.
- **Validate a cheap proxy at the ARM level, never per cell** — round-1 truncation passed `proxy_lift_corr >= 0.6` per cell and still ordered arms near a coin.
- **Widen candidates semantically — the lever with no measurement cost.** Different evidence framing, node or edit vocabulary, not a reworded instruction, raises arm SD for free.
- **A cell whose constant-answer floor exceeds its origin is disqualified** (`application/diagnostics/seed_screen.py::rewards_collapse`); the *gap* is the reading. `promptpotter-self/panel/dataset.yaml::panel` records each retired seat's grounds — cite those.
- **A cell's score moves in STEPS, and an unchanged prompt can take one** (`domain/l4/proxies.py::compute_outer_proxies` reads the promoted parent). Before crediting an arm, open the cell's `review.md`: a clone or a non-binding parameter is noise with the arm's name.
- **Compare arms on the cells BOTH met**; **a win is a few rows**; **a flat round 2 does not mean the search is done**.
- **A limit on one axis binds all** (root `CLAUDE.md` `<one-budget>`): price every panel proposal in wall clock *and* dollars.

## Decision points and the cost ladder

A **decision point** is one inner round frozen in `rounds/round_NNNN.json`: the parent, the evidence the optimizer saw, the rows and the parent's level on them. Grading an optimizer prompt on one costs one `l1_generate` call plus a scoring pass. The bank is the inner rounds under `.promptpotter/.inner/` carrying a parsed `l1_generate` call, less round 0 (`os.walk` — a glob cannot see a dot-directory). It is the cheap screen; campaigns confirm survivors. The one-step → trajectory link is unvalidated: the pilot (30 points, the parent twice, one degraded prompt once) gives the noise floor and a known-sign effect first.

**Always run the cheapest unexhausted rung**, never rung 4 while rung 0 has content; prices come off the ledger.

| Rung | What lives here |
|---|---|
| 0 — free | Walking `.promptpotter/`: harvesting, retrospective reads, identity and plumbing fixes |
| 1 — cents | Single-prompt probes over a handful of decision points; the pilot |
| 2 — one panel | One challenger against the pinned baseline |
| 3 — one round | A full outer round |
| 4 — deferred | Wider panels, longer campaigns, a new benchmark |

**We are not optimizing the long campaign** (operator): push the first few rounds. **Cut the panel to one seed only when data shows a small arm×seed interaction** — "best seed" is outcome selection, and un-pairing costs more than twice the cells.

## The playbook

### 1. Read the current L1 optimizer prompt

`resolved_prompts["l1_generate/1"]` in potter's `pipeline.yaml`; note its `{{slots}}` and cross-check `application/optimizers/potter/dispatch/injections/registry.py::injection_table`. An unreferenced slot is wasted load; an unregistered one raises at load (`validate_template`).

### 2. Read the round's audit trail

Render the prompt the LLM saw from the ledger's `llm_call` record (`payload.template_fields` against `payload.variables`); take parsed candidates, composite scores and the `l1_critique` block from `round_NNNN.json`.

### 3. Read PoBB stream + ledger

All candidates to `n_min` with near-identical intervals = **flat stratification** (L1 proposed nothing meaningfully different). One running away early: read *why* it differed. The ledger records every escalation fire.

### 4. Read the critique

A critique disagreeing with the composite is a scoring problem, not an L1 problem.

### 5. Classify the failure mode

Pick the *single closest*; if two apply, the upstream one. Every edit rides `changes_description`.

| Mode | Symptom | Edit |
|---|---|---|
| **★ Semantic restatement** (check first) | Valid, string-distinct candidates all testing one idea, each at +0.000. No counter sees it: `idea_fingerprint` is word overlap, so no `repeat_variant` rejection is not novelty. Judge the `changes_description` texts across rounds yourself — rung 0. | State the **hypothesis** under test, differing in hypothesis from every `mutation_memory` row: another panel cited, another node, another edit vocabulary. |
| Off-task | Candidates contradict the frozen `task_context`. | Quote one phrase of `task_context` in the rationale; never summarize it at the prompt site. |
| Ignoring critique | Round N+1 repeats the failure round N's critique named. | Name the critique bullet each candidate addresses. |
| Surface-only | Identical `pipeline_overlay`, cosmetic text change, composite ≈ parent. | Name the axis moved and the effect expected, grounded in `mutation_memory` / `axis_memory`. |
| Params overreach | `param_scope_discipline` (`validators/l1_behavior.py`) scores a param mutation while a prompt field sat unmutated two rounds. | Name the prompt-field evidence exhausted first. |
| Critique-score divergence | Critique says A, composite says B. | Not L1: route to `{dataset}/dataset.yaml::campaign.scoring`. |

Parse failures, no-ops and verbatim duplicates were measured absent; spend no edit there without fresh evidence (`l1_yield` below 1.00).

### 5b. The round-trace checklist — walk it before reporting findings

Code enforces `optimizers/potter/validators/l1_strict.py` and `overlay_failures()` (`application/bench/children.py`); **for everything else your analysis IS the gate.**

- **Evidence availability.** `axis_memory` needs ≥1 prior archive measurement; `runtime_failures` empty in round 1 while sibling forks failed means broken inheritance (`bench/cycle.py::Cycle.runtime_failures`). `critique` / `escalation_panel` are empty in round 1 by design.
- **Near re-proposal.** `l1_config_not_in_runtime_failures` catches exact `(param, value)` matches only; flag an unjustified value near a failing one.
- **Standing constraints** (PEAKED axes, param axes last, numeric envelopes) — read off `resolved_prompts['l1_generate/1']` at review time; flag each crossing without its demanded evidence.
- **Intra-round paraphrase.** Jaccard ≥ 0.5 over a pair's `changes_description` → `intra_round_paraphrase`; a theme word (verify, check, restate) in ≥ N/2 → `theme_mode_collapse`.
- **Format integrity.** LaTeX escapes survive; no template placeholders in values; `pipeline_overlay` keys are real `param_keys`.

**Report violations as a checklist at the TOP of the reply, before any narrative:**

```
L1 violations on round N (cycle <id>):
  ✗ peaked_axis_violation (C1.1: target_axis=llm_only.max_tokens, axis marked PEAKED, no critique rebut)
  ✗ unjustified_param_mutation (C1.1: critique didn't name max_tokens, runtime_failures empty)
  ✓ schema_compliance (no forbidden-axis or type-mismatch issues)
  ⚠ intra_round_paraphrase (C1.2 ↔ C1.4 Jaccard 0.58 — verify/proof theme)
```

### 6. Propose the edit, predict, re-run one round

A unified diff against `resolved_prompts["l1_generate/1"]`, a one-line prediction, then one round (`python -m promptpotter resume`; `new` only after a config/prompt change). Open `round_(N+1).json`: prediction held → lock it in; else reclassify — **never reword a failed edit**.

### 7. Authored mode — you are the proposer

**`optimization.arm_gate: author`: every round holds, no generator runs, and the arms are the ones you or the operator submit** ("you are the proposer", "you write the candidates", "self-potter, hand-authored"). Sibling **review mode** (`arm_gate: review`): the generator drafts, the round holds, you keep drafts by index and/or add arms. Scoring, critique and escalation run unchanged.

Launch with `--set arm_gate=author`, read the hold in the readout, answer with `submit-arms --arms arms.yaml`. An arm edits the round's parent (`pipeline_overlay: {l1_critique: {instruction: …}}`): one per hypothesis, a combined arm only beside its parts.

**Three checks before an authored arm earns a cell, in order — each has failed a hand-written arm:**

1. **The lever must be in the node's sight** — read its `possible` set in `dispatch/layout.py` and its rendered prompt in a banked round.
2. **The lever must bind in the inner benchmark** — a budget axis the solver never reaches, or one the inner `{dataset}/dataset.yaml::pipeline` does not open, moves nothing. Settle headroom with `ab` (the parent at two values) first.
3. **The edit must change what the node EMITS** — run it on banked decision points and compare its output with the parent's on the named failure. No output change, no cell change.

## Edit etiquette (the non-negotiables)

- **One edit per pass.**
- **No new response-contract field from the prompt side.** `L1Variant` is `extra="forbid"` (`dispatch/schemas.py::L1Variant`); its `targets_rows` (binding a variant to one `l1_critique` cluster's rows) is the shipped STRUCTURAL answer to semantic restatement — do not re-prescribe it in prose. A real new field moves the Pydantic model, both `answer_format`s and `resolved_schemas` in **one commit**. Prefer `changes_description`.
- **No backward compatibility** — change a slot name everywhere (root `CLAUDE.md` STOP).
- **Slots flow through `DispatchHub`**: a new `{{slot}}` is an `@signal` renderer under `dispatch/injections/`; never summarize a field at the prompt site.
- **L1 owns `pipeline_params`.** A framing diagnosis is written "→ operator should revise task_context to X" and stops there — no layer writes it.
- **Cycle hash awareness.** An optimizer prompt edit changes `JobSearchPoint.content_hash` for the next round, not the target cycle's origin hash. At L4 an inner node's prompt body or config moves `_identity_config`'s `inner_origin` (§ Freeze the inside, iterate the outside); outer families, `checkin`, node descriptions and a release do not. Suggest `--fork-on-divergence` to keep prior runs comparable.
- **No hidden defaults; trim to invariants, not history** (no `# was: …`).

## What L4 does *not* do

- Generate candidates (L1's job), refine `task_context` (operator-frozen) or replan (L3's — L3 firing means the plan-space was wrong).
- Modify `task.md` or per-dataset configs (cycle identity and the scoring contract).
- Re-score past rounds — swap the formula in `{dataset}/dataset.yaml::campaign.scoring` and let the next round recompile.

## Useful pointers

Repo-relative: layer contracts `promptpotter/application/optimizers/potter/CLAUDE.md` · slot flow `docs/developer/dispatch-hub.md` · the measurand and what a panel may claim `docs/specs/l4-outer-loop.md` (read before trusting any outer number) · the identity fingerprint `docs/operations/persistence-and-state.md` · style `docs/developer/conventions.md`.
