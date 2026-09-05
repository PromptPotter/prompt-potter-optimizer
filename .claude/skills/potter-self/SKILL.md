---
name: potter-self
description: PromptPotter L4 self-optimization — Claude + operator reading a round's artifacts, diagnosing why candidates underperformed, and deciding what evidence to buy next before spending another round. Use whenever the operator pauses at the round-1 gate, halts the loop to review, mentions L4, `promptpotter-self`, L1 stall, mode collapse, weak candidate stratification, identical / near-duplicate candidates, candidates ignoring the critique or task_context, parse failures on the l1_generate JSON, or wants to tune `l1_generate/1` in `promptpotter/assets/optimizer/pipeline.yaml`. Also use when the operator opens a `round_NNNN.json`, asks "why did L1 do that?", says "let's improve the optimizer prompt", or is weighing what experiment to run next — panel width, seed count, how many cells, whether a result is real, or why past runs never accumulated — even if "L4" is not named explicitly. Each searchpoint costs real LLM spend, so this collaborative review is the substitute for an L4 LLM-driven layer; do not skip it just because the operator did not utter the letter L4.
---

# L4: collaborative review of L1-generate

L4 is **not another LLM layer**. It is the operator (with you, Claude, as analyst) reading the round's artifacts and editing the `l1_generate` optimizer prompt before spending another searchpoint budget. Each candidate is expensive — measure first, mutate the prompt with cause.

This skill walks the loop: **read → classify → edit → predict → re-run one round → compare**. One concrete edit per pass. Multiple changes at once destroy the signal.

## How to read this file

**The measured sections are findings; the planning sections are a guideline, not a contract.** Everything below is one line of reasoning from one corpus, written down so it survives across sessions instead of being re-derived each time. Three standing instructions:

1. **Criticize it.** Each claim names the evidence it stands on. If you can knock one down, that is the file working — and it has happened: several of the best pieces here started as the operator pushing back on what this file said.
2. **When the operator proposes something else, COMPARE — do not replace.** Put the new idea beside the relevant section, price both on the cost ladder, and say which wins and why. A proposal supersedes this plan on an argument, never on recency.
3. **Prefer the answer already on disk.** Most numbers here came from walking `.promptpotter/` for a few seconds. Spending money to learn what the archive already knows is the failure this file exists to prevent.

Every figure below carries its corpus size and date. **Recompute before citing** — the corpus grows, and at least one figure has already moved materially.

## Mental model

The optimizer is three nested generation loops (`promptpotter/CLAUDE.md`):

- **L1** (`l1_generate`) generates candidate prompts with cause from its evidence surface — the panels its live layout renders (`NODE_LAYOUTS["l1_generate"].floor`, `domain/l1_layout.py` — read the membership there) — with `task_context` from L2 and `plan` from L3.
- **L2** (`l2_context`) refines `task_context` on L1 stall.
- **L3** (`l3_plan`) replans on L2 stall.

**Never name a panel from memory.** The citable set is *derived* — `@signal(..., citable=True)` intersected with the node's live layout by `citable_fields` (`dispatch/injections/registry.py`). A panel that does not render invites a fabricated citation, which is exactly how `sibling_yield` — a name this skill carried for weeks — went on being cited after it was deleted from the code.

L4 is the human-in-the-loop review that happens **between rounds**, especially at the round-1 gate. The L1 optimizer prompt template at `promptpotter/assets/optimizer/pipeline.yaml → resolved_prompts["l1_generate/1"]` is what L4 edits — *not* L2's or L3's surfaces, *not* `pipeline_params`, *not* the `task_description.md`.

## Artifact map (what to read, in order)

Cycle root: `.promptpotter/projects/{tenant}/campaigns/{campaign_id}/cycles/{cycle_id}/`. The active pointer is `.promptpotter/projects/{tenant}/.workspace/active_session.json`. An L4 inner cell lives under the flat `.promptpotter/.inner/{key}/…` sandbox, never nested under its outer cycle.

| Step | File | What you extract |
|---|---|---|
| 1 | `promptpotter/assets/optimizer/pipeline.yaml` → `resolved_prompts["l1_generate/1"]` (outer set: `assets/optimizer/sets/self_optimizing.yaml`) | The current L1 optimizer prompt template — the thing you will edit |
| 2 | `{cycle_dir}/rounds/round_NNNN.json` | Per-round audit: parsed candidates, per-candidate scores, `overlap`, `separable`, critique text. **No rendered prompt** — see row 4 |
| 3 | `{cycle_dir}/.runtime/streams/round_NNNN_p_best.jsonl` | PoBB elimination stream — did variants stratify or collapse? Which got eliminated first? |
| 4 | `{cycle_dir}/.runtime/ledger.jsonl` | The cycle event log — escalation firings, decisions, spend. There is no `signals.jsonl`. **The ONLY place the rendered optimizer prompt survives**: each `payload_kind: "llm_call"` record carries `template_fields` + `variables` (render one against the other), and the `llm_call_start` beside it carries `prompt_chars`, `injection_chars`, `injection_dropped` and `injection_silent` — the panel-by-panel breakdown of what the node was actually handed. |
| 5 | `{cycle_dir}/dashboard.json` | Round-by-round composite trajectory + recent rules |
| 6 | `{cycle_dir}/prompts/{node}.yaml` | Current `PromptTemplate` for each pipeline node — the *target* of L1's mutations (read-only here) |

Reads happen by opening files; `evidence` is the one read VERB, because a comparison ACROSS campaigns is in no single file. The file tree is the dashboard.

## Live-run supervision

**The infrastructure is done; the optimizer *application* is not.** The loop, seams, recursion and scoring gateway all exist and are green. What remains is making the optimizer *behave well*, and that is found empirically: run it, collect the data, read what the loop actually produced, fix the bug at its ROOT, re-run. Expect several restarts; that is the loop, not a failure.

**Run `new promptpotter-self` ONCE, in the FOREGROUND, to completion — never as a killable background task.** A harness/OS kill orphans whatever inner run is open; the reaper stamps it `producer_vanished` (a `FAILED` outcome — correctly excluded from scoring, so the headline is not corrupted, but NOT cached). A relaunch lands back on the *same* inner campaign and continues from the rounds already banked, because an inner campaign is keyed on the cell it runs, the overrides it runs under, and what it is FOR — a panel cell or a PoBB backfill (`runner/inner/spawn.py::inner_campaign_id`). What a kill costs is the round it interrupted, not the cell. And because the outer `cycle_id` is a hash of optimizer prompts + benchmark, every re-`new` with unchanged prompts collides on ONE `cycle_id` and ONE `.inner/` sandbox — the lineage tree then renders the same inner seed-runs under N campaign headers, so "N campaigns, identical stats" is one measurement shown N times, not N runs. **To iterate a live run, `resume` it;** only `new` again after a prompt/config change, which mints a fresh `cycle_id`.

**The cadence is SELF-FIRING, and the interval is WORK rather than a wait.** Schedule your own wake-ups the moment a run starts, and each wake IS a full pass over the reading list below. Between ticks keep investigating — fan out over the fresh dashboards and measurement files, chase the newest anomaly. An idle wait is the wasted-run failure mode this guards against: the bugs show themselves *while it runs*, and catching one early buys a kill-fix-restart before the whole spend drains. **A passive log Monitor does NOT count as supervision** — it fires only on patterns you predicted, and every real bug so far (estimator inconsistency, evidence starvation, proxy annihilation) came from reading the run's own measurement files, not from a grep hit. Role split: the operator is the developer/user; you own everything else.

**Default the fix to the prompts** (`promptpotter/assets/optimizer/` — `pipeline.yaml::resolved_prompts` for the inner set, `sets/self_optimizing.yaml` for the outer one). Reach past prompts to a code fix ONLY when the data shows a structural cause — broken information flow, a missing analysis, a wiring gap. Name that cause before touching code; do not add infrastructure to paper over a prompt problem.

### The per-checkup reading list — every tick reads ALL of these, not just the log tail

**A checkup that only greps the run-readout tail is NOT a checkup** (operator-mandated 2026-07-02). Each tick, open the newest outer `{cycle}/.runtime/cache/rounds/round_NNNN.json` for each tier's output — and take the rendered INPUT from `.runtime/ledger.jsonl` (row 4 above), because the cache's `l1_generate` entry is a synthesized stub on any replayed round:

1. **`l1_generate`** — rendered input: are the panels populated or empty? `injection_dropped` on the `llm_call_start` record answers that directly, and **a name in it that is also `L1_MANDATORY` is a stop-and-diagnose** — `rendered_prompt` refused whole is how the generator ends up rewriting prompts it was never shown. For the OUTER generator, is `inner_narratives` present with a story per seed rather than bare stat lines — the primary evidence an optimizer-prompt edit must ground on, not the scalar per-seed delta? Then raw output, parsed variants: `evidence_grounding.field` in the real enum? citations quoting text that EXISTS in the rendered input — and quoting the panel they NAME, not another one? hypotheses distinct, not one idea relocated? `changes_description` actually REPORTING the override emitted beside it? any hallucinated node/param?
2. **`l1_critique`** — the input carries the evidence, and WHICH panel is the evidence depends on the level: inner reads SAMPLE TRANSCRIPTS + MODEL REASONING, outer reads INNER RUN NARRATIVES. The two are a matched pair, each silent where the other fires (`panels.py::_inner_narrated`), because transcripts are selected by a MISS and one level up a miss is a placeholder artifact. Output `priority_fix` / `failure_highlights` must quote CONCRETE evidence — a reasoning step, a premise — not recycled labels, and `priority_fix` must name a steer the generator is ALLOWED to make: an edit to the inner optimizer's own job, never one naming the benchmark's vocabulary or answer labels.
3. **Scoring** — per-candidate `candidate_scores` (accuracy, θ, θ_se, `mean_fitness_ci_lo`), the **matched-parent** comparison (never the cross-subset round-0 origin — subset drift reads as lift), the PoBB stream (`p_best` moving off 0.5?), `decisions` (cuts firing, on the right arm?).
4. **`l2_context` / `l3_plan` when fired** — validator failures (`paraphrase_repeat`, `dangling_trigger`), whether the `task_context` delta is evidence-anchored, plan text sane and within its render cap.
5. **Spot-check ≥1 inner campaign per outer sample batch** — the same four reads one level down, under `.inner/<key>/…/campaigns/`.

**STOP-AND-DIAGNOSE, not keep-watching:** `raw_chars: 0` / an empty candidate list · an outer sample returning in ~0.0s (stale-cache reuse) · off-enum grounding fields · any optimizer call > 2 min · a headline Δ that disagrees with `matched_parent_*` / `improved`.

A quiet outer round is normal — it is awaiting a multi-minute inner campaign, and the cycle heartbeats its own ledger ("inner rX/Y · best Z%") while it waits. General hang triage: [`docs/operations/persistence-and-state.md`](../../../docs/operations/persistence-and-state.md) § Diagnosing a live or stuck run.

## Why experiments did not accumulate — and what changed

Read this before proposing any new run. It is the reason a year of panels produced nothing adoptable.

- **The baseline moved with the treatment.** `_identity_config` hashed the whole optimizer manifest, so the optimizer prompt — the thing under study — sat inside the *origin* fingerprint. Editing it voided every banked outer cell. Each run therefore started from zero **by construction**, and no amount of care in running them could have changed that.
- **The panel could not measure its own precision.** On 73 cells (2026-08-15), split-half reliability of the arm-level mean is ~0.18 over 11 arms while the parametric decomposition implies ~0.7 — at this corpus size neither is resolvable. So the honest statement was never "the panel is bad"; it was "we cannot say how good the panel is", which is worse, because it makes every leader unfalsifiable.
- **FIXED 2026-08-15 — the fingerprint was narrowed, not removed.** It now reads what the inner optimizer nodes *resolve to* (prompt body, resolved schema, config) plus the estimator's own source, instead of the whole manifest plus `APP_VERSION`. Still voids: an inner node's prompt body or config, `NODE_LAYOUTS`, panel prose, estimator source, `inner_tasks.yaml`, the benchmark's `pipeline.yaml` + `campaign.yaml`. No longer voids: `checkin`, node descriptions, `available_models`, a release. Editing `sets/self_optimizing.yaml` never did.
- **Narrowing it was not enough, and the ratchet is what finishes the job.** Count the fingerprints before trusting any cross-campaign number: every `promptpotter-self` campaign on disk carries a *different* `inner_origin`, so not one has ever replayed another's cells. Each re-measured its origin under whatever revision the engine happened to be at, which is also the honest reading of the run-order confound `evidence` reports and of any "replicate" spread — an arm held constant while the instrument moved is not a noise measurement. The MINT now says so before the spend (`jobs/mint.py::_warn_on_novel_instrument`): a novel instrument names how many prior campaigns on the dataset it matches, so "nothing accumulates" arrives as a number you are handed rather than a discovery weeks later. Pinning the fingerprint in a test was tried twice and removed twice — its VALUE moved on a third of all commits, and the roster of hashed modules was a name census that reddened on every rename. The prompt half is WALKED (`registry.py::_RENDERER_MODULES`, with an import-time orphan check), so only the estimator roster (`connectors/promptpotter.py::measurement_modules`) can lose a member quietly, and the mint counts what that costs before the spend.
- **So the loop is: FREEZE the inside, iterate the outside.** Inside the fingerprint — dispatch and panel prose, `NODE_LAYOUTS`, the estimator source, inner optimizer prompts, `inner_tasks.yaml` (the seed roster included), the inner benchmark's config — is a corpus reset; batch those and re-measure once, deliberately. Outside it, `assets/optimizer/sets/self_optimizing.yaml` is the whole L4 edit surface and costs nothing banked, so refine it as often as you like. It still trips the RESUME divergence gate, which is a different mechanism: an outer-prompt edit costs you the cycle (`new`, never `resume`) and keeps the archive.
- Found in passing and closed: the inner benchmark's `campaign.yaml` was **completely unhashed**, so editing its `scoring` formula or `pobb_epsilon` would have silently pooled measurements taken under different rules.

## What the outer panel can and cannot tell you (73 cells / 17 arms / 6 seeds, 2026-08-15)

> **Recompute this section before trusting it — `promptpotter evidence --campaign <id> ...` now
> answers most of it on demand.** The variance split is `variance.{cell_effect_sd,arm_effect_sd,
> residual_sd}`, the resolving power is `power.{paired_se,min_detectable_effect,
> cells_for_largest_gap}`, and the replicate and run-order reasoning are `replicates` /
> `order_confound`. The figures below are a READING taken on 2026-08-15, not a standing fact:
> this skill's own rule is to prefer the answer already on disk, and that answer now has a verb.

- **Variance structure.** Seed effect SD **0.198**, arm effect **0.109** (both shrunk by their own estimation error), residual **0.170** — so seed variance is ~3.3x arm variance, and pairing every arm across the same cells is what makes the comparison possible at all. A typical two-arm gap (0.154 logits) resolves at **~10 paired cells**; the panel runs 6. Un-paired it would take **22.9**. The earlier "~35 cells/arm" figure came from a 39-cell read where the arm effect measured 0.077; it roughly doubled as the corpus grew, so **the panel is closer to working than it used to look** — and 6 → 10 cells is the cheapest move on the board.
- **Read the SHAPE as well as the scalar — it is legible at n=6 where the scalar is not.** Every cell records a per-round `improved` verdict (a *within-round* paired comparison against the matched parent on the same samples), so it touches neither the θ anchor nor the re-drawn subset. A 6-cell panel carries ~24 of those against 6 scalars. Rendered per cell by `application/runner/inner/spawn.py::_lift_shape`.
- **Target shape:** most cells lift in round 1 (most headroom, cleanest evidence), about half again in round 2, stragglers in round 3, thinning as they saturate. **Measured: `r1 3/6 · r2 1/5 · r3 2/3 · r4 1/3`** — rounds 3-4 behave; round 1 lifts only half the time and round 2 nearly flatlines. **That is the live defect and it is an `l1_generate` problem** — diagnose it from candidates already on disk, not from a new run.
- **The scored term is `mean_round_delta`**, and what it rewards is lifting **early**: a cell that climbs in round 1 and holds scores above one reaching the same place in round 4. So edits that make L1 find its hypothesis sooner are worth more than edits that make it find a better one later. (Why that term and not another: [`docs/specs/l4-outer-loop.md`](../../../docs/specs/l4-outer-loop.md) § The measurand.)
- **Validate any cheap proxy at the ARM level, never per-cell.** Truncating cells to round 1 is 2.2x cheaper per verdict and *wrong*: per-cell correlation 0.663 (passes the usual `proxy_lift_corr >= 0.6` bar) but **arm-effect correlation 0.371**, ordering 13 of 21 pairs against 10.5 for a coin. Put the bar where the decision is made.
- **Widen candidates semantically — that is the lever with no measurement cost.** L1 emits incremental edits, so arms differ by less than the instrument can see. Different evidence framing, a different node targeted, a different edit vocabulary — not a reworded instruction — raises the arm SD for free. (A quieter instrument is *also* worth buying now; see the plan below. The two are complements, not alternatives.)
- **A panel cell whose constant-answer floor exceeds its origin accuracy is disqualified** (`application/seed_screen.py::rewards_collapse`) — a candidate that stops reasoning and hedges to one label then outscores the parent, every round. The raw floor is the wrong reading; the *gap* is. Of the first six seeds, three were retired and only **one** on this criterion — `inner_tasks.yaml` records the grounds per seat and explicitly forbids citing the collapse verdict for seed-5.
- **Calibration — what a WIN is worth here.** A winning inner round buys **1-4 rows in 28** (`+0.036 / +0.071 / +0.107 / +0.143` are the only positive matched-parent lifts ever recorded). Improvement is granular and infrequent; do not read a +0.036 round as noise, and do not expect an edit to produce more than a few rows.
- **Where lift lands says how long to run.** The round carrying a campaign's best accuracy is spread uniformly across the whole budget, and the strongest run on disk peaked on its LAST round, still climbing. A flat round 2 is not evidence the search is done.
- **Budget: a limit stated on one axis binds all of them** (`<one-budget>`, `docs/developer/conventions.md`). "Only $0.50" against a five-hour panel is a budget increase. Price every proposal in wall-clock *and* dollars.

## The plan, as a proposal — not a contract

**Decision points, not campaigns.** A campaign already *is* ~4 questions asked in sequence with only the last answer written down, and the ratio between what is paid for and what is kept is the whole argument. Count both before proposing anything: the outer layer everyone reports on is a couple of dozen candidates, while the inner rounds underneath it — each one a scored, paired optimizer decision — run into the hundreds. Recount rather than cite; the gap widens with every run.

A **decision point** is one such moment frozen — the parent target prompt, the evidence package the optimizer saw, the rows that round scored on, and the parent's already-measured level on exactly those rows. Every field is already written to `rounds/round_NNNN.json` on every round. Grading a prompt on one = render it against the frozen evidence, one `l1_generate` call, score its candidates on the frozen rows, take the best candidate's lift over the frozen parent. **~2 min and ~$0.025, against 11.4 min and $0.055 for a cell that yields one number.** The bank is large and unspent — walk `.promptpotter/.inner/` (`os.walk`; a glob cannot see a dot-directory) and count the inner rounds carrying a parsed `l1_generate` call, then subtract one per campaign, since round 0 has no parent to be a decision against.

Why it would accumulate: the frozen bank is the *ruler*, not the origin, so editing the prompt voids nothing — it is the treatment. Replicates rise from ~6/arm to 100+, each perfectly paired and near-deterministic (`inner_optimizer_temperature: 0.0`). Depth is preserved rather than averaged — a round-4 state has a strong parent and a crowded `mutation_memory`, so "can it still find something new" becomes its own sample.

**Two tiers, not a replacement.** Decision points are the cheap daily screen; campaigns stay the weekly confirmation on the 1-2 survivors, because a frozen bank cannot see self-consistency — whether a prompt reaches positions it can then exploit. Cheap screen → expensive confirm. Reading the screen as the whole instrument is the mistake.

**Open, and honestly so.** The one-step→trajectory link is **unvalidated**, and the round-1-truncation result above is a live warning shot (the counter-argument: that test truncated *and* kept n=6, whereas this trades depth for n). How much pairing on an identical frozen state cancels has never been measured, because no two prompts have ever answered the same frozen state. Goodhart on a frozen bank is real. **The ~30-question pilot resolves the first two: harvest 30 points, run the parent twice and one deliberately degraded prompt once** — parent-vs-itself gives the noise floor, parent-vs-degraded a known-sign effect. Tier 1, ~$2.3. It validates the scheme or kills it before anything is built on it.

### The cost ladder

**Always run the cheapest unexhausted experiment.** As cheap rungs are exhausted the frontier price rises, and an experiment previously too expensive becomes admissible *then* — not before. Cost focus is simultaneously efficiency and time focus.

| Rung | What lives here |
|---|---|
| 0 — free | Anything answerable by walking `.promptpotter/`: harvesting, retrospective reads, identity and plumbing fixes |
| 1 — cents | Single-prompt probes over a handful of decision points; the pilot above |
| 2 — one panel | One challenger against the pinned baseline; topping up a promising one |
| 3 — one round | A full outer round; the weekly confirmation |
| 4 — deferred | Wider panels, longer campaigns, a new benchmark |

**Never propose a Tier 4 rung while Tier 0 has content.** Read per-unit prices off the ledger, not off this table, which deliberately carries none.

**Corollary, operator-stated: we are not optimizing the long campaign.** Push performance in the first few rounds — the budget already running — and collect longer horizons as an additional gain afterwards. `max_inner_rounds` stays where `inner_tasks.yaml` sets it, and the measurand already agrees.

### The seed question — open, and two traps

Whether to cut the panel from 6 seeds to 1 turns entirely on the **arm×seed interaction**, which is invisible today because the instrument never replicates an (arm, seed) pair — it is content-addressed, so asking again replays. Decision points replicate *within* a seed by construction, so they measure that term directly. **Do not cut on intuition; cut when the data says the interaction is small, and get the cut for free.**

- **"Best performing seed" is outcome selection.** The highest-mean bank is where *every* arm looks good — hence the least discriminating, not the most.
- **Un-pairing is the expensive way to save money.** Letting arms draw their own banks stops the seed main effect cancelling: 22.9 cells/arm instead of 9.7 for the same resolving power.

## The six-step playbook

### 1. Read the current L1 optimizer prompt

Open `promptpotter/assets/optimizer/pipeline.yaml` and locate the `l1_generate/1` body under `resolved_prompts`. Note which `{{slots}}` it references. Cross-check against `application/optimization/dispatch/injections/registry.py::INJECTIONS` so you can name what data each slot delivers. A slot the template never references is wasted load; a slot the template references but `INJECTIONS` does not register raises at load time (already caught by `validate_template`).

### 2. Read the round's audit trail

Capture the **rendered prompt** (what the LLM actually saw, not the template) from `.runtime/ledger.jsonl` — the `llm_call` record for the node, rendering `payload.template_fields` against `payload.variables`. It is **not** in `round_NNNN.json` and **not** in the round cache; on a replayed round the cache's `l1_generate` entry is a `payload_kind: "synthesized"` stub whose `input` is `{source, round}`, which is honest about no call having fired and useless as evidence. Take the **parsed candidates**, **per-candidate composite scores** and the **`l1_critique` block** from `round_NNNN.json`.

### 3. Read PoBB stream + ledger

`{cycle_dir}/.runtime/streams/round_NNNN_p_best.jsonl` shows the elimination order. If all candidates lasted to `n_min` with near-identical posterior intervals, you have **flat stratification** — L1 didn't generate meaningfully different proposals. If one ran away early, look at *why* it differed. `.runtime/ledger.jsonl` records every escalation rule fire.

### 4. Read the critique

The `l1_critique` block names which candidates won and failed and cites the axis. If the critique disagrees with the composite scores, the scoring formula and the critique's framing are out of sync — that is a scoring problem, not an L1 problem.

### 5. Classify the failure mode

Pick the *single closest* match. If two apply, pick the one upstream of the other.

#### ★ Semantic restatement — one hypothesis in N wordings (THE LIVE DEFECT)

Symptom: candidates are valid, well-formed, distinct as strings, and all test the *same idea*. Measured: ten edits that each asked the target to reason further before answering — one hypothesis, ten wordings, every one +0.000. The generator was re-proposing roughly a third of the time.

**No counter detects this.** `idea_fingerprint` is content-word overlap and caught **0 of 15** of those pairs, so a clean `l1_n_repeat` is *not* evidence of novelty. This is what produces the broken lift shape (`r1 3/6 · r2 1/5`).

Diagnosis, and it is free: read the `changes_description` texts across rounds yourself and judge them semantically. With 107 decision points on disk this is a Tier-0 retrospective — no run required.

Edit: must ride `changes_description` (no new fields — see Edit etiquette). Require it to state the **hypothesis** under test, and to differ in hypothesis rather than wording from every row in `mutation_memory`. Aim at semantic width: a different evidence panel cited, a different node targeted, a different edit vocabulary.

#### Off-task — candidates ignore `task_context`

Symptom: candidates contradict the framing L2 set. Root cause: the slot renders but the template never cites it as a constraint. Edit: require the rationale to quote one phrase from `task_context`. Keep the injection through `DispatchHub` — do not summarize it at the prompt site.

#### Ignoring critique — candidates repeat last round's mistakes

Symptom: round N+1 exhibits the exact failure round N's critique called out. Root cause: critique renders as background, not as a constraint. Edit: require each candidate's `changes_description` to name which critique bullet it addresses.

#### Surface-only changes — rephrasing without substantive intent

Symptom: identical `pipeline_params_override`; only prompt text varies cosmetically; composite ≈ parent. Root cause: the template asks for "improvements" without forcing the candidate to declare its mutation surface. Edit: require `changes_description` to name the axis moved and the effect expected, grounded in `mutation_memory` or `axis_memory`.

#### Pipeline-params overreach — touching locked axes

Symptom: `validators/l1_strict.py` flags a mutation outside `escalation_panel.params_unlocked`. Edit: render `params_unlocked` as a fenced list and state the consequence — "mutations on locked axes are dropped before scoring".

#### Critique-score divergence (out of scope from L1)

Critique says A is best, composite says B. **Not an L1 problem.** Route to `datasets/{name}/campaign.yaml::scoring` and stop reading L1 artifacts.

#### Footnote — generator mechanics, measured absent

Parse failure, no-ops and verbatim duplicates: **zero** over 6 inner campaigns / 17 L1 rounds, `l1_yield` 1.00. Do not spend an edit here without fresh evidence that one has returned; the candidates are well-formed, they just restate rather than explore.

### 5b. The round-trace checklist — walk it before reporting findings

Skipping these has historically let evidence-free or rule-violating proposals through unflagged. None
is blanket-rejected by code; **for the unenforced ones your analysis IS the gate.** The enforced set is
the registry itself (`optimization/validators/l1_strict.py`) plus `validate_overrides()`, which locks
`model` / `provider` unconditionally — read the registry before assuming a check is unenforced.

- **Evidence availability.** For round 1 (especially a fresh fork), does the rendered input actually
  carry the signals a candidate claims to consult? `axis_memory` is present iff `AxisIndex.ensure_for`
  found ≥1 prior archive measurement (empty on a backend's first cycle). `runtime_failures` is present
  iff this cycle produced one OR `Cycle.start` inherited from sibling forks — **empty in round 1 while
  siblings DID produce failures means the inheritance path is broken** (`sibling_wounds.py`,
  `_rf_matches_current_config`). `critique` / `escalation_panel` are empty in round 1 by design.
- **Re-proposal of known-failing configs.** `L1_CONFIG_NOT_IN_RUNTIME_FAILURES` catches EXACT
  `(param, value)` matches only — a *near* value is legitimate exploration, so flag one proposed near a
  known-failing value without justification rather than expecting a rejection.
- **The generator's standing constraints** — PEAKED-axis discipline, param-field axes as a last resort,
  the numeric envelopes. **Read them off `resolved_prompts['l1_generate/1']` at review time, never off
  any doc:** it is an L4-searched surface, so a quoted constraint is one that has already moved. For
  every candidate that crosses one, read its `evidence_grounding.citation` and ask whether the evidence
  the prompt demands is actually quoted; if not, flag it by the constraint's own name.
- **Grounding actually grounds.** A citation must be a real quote from the named `field`, and the field
  must be one the round's layout rendered (`citable_fields`). `field=stall_exploration` is valid only
  at `exploration_budget ∈ {normal, wide}`. Not a strict validator by operator direction — the model may
  read all input as evidence — but a citation naming a field absent from the rendered input is a
  fabrication.
- **Intra-round paraphrase.** Jaccard of word-sets (lowercase, `\w+`, len > 2) over `changes_description`
  for each pair; ≥ 0.5 → `intra_round_paraphrase`. Below threshold, watch shared THEME words (verify,
  check, restate, validate) — ≥ N/2 candidates carrying one → `theme_mode_collapse`. Nothing enforces
  either, and `idea_fingerprint` is blind to both (see § semantic restatement).
- **Format integrity.** LaTeX escapes survive (`\boxed{N}`, not `oxed{N}`); no template placeholders
  (`{x}`, `[insert]`, `<query>`) in prompt-field values; `pipeline_params_override` keys are real node
  `param_keys` (`L1_SCHEMA_COMPLIANCE` catches invalid ones).

**Report violations as a checklist at the TOP of the reply, before any narrative** — the glyph makes it
scannable and the parenthetical lets the operator verify in the trace:

```
L1 violations on round N (cycle <id>):
  ✗ peaked_axis_violation (C1.1: target_axis=llm_only.max_tokens, axis marked PEAKED, no critique rebut)
  ✗ unjustified_param_mutation (C1.1: critique didn't name max_tokens, runtime_failures empty)
  ✓ schema_compliance (no forbidden-axis or type-mismatch issues)
  ⚠ intra_round_paraphrase (C1.2 ↔ C1.4 Jaccard 0.58 — verify/proof theme)
```

### 6. Propose the edit, predict, re-run one round

Write the edit as a unified diff against `resolved_prompts["l1_generate/1"]`. State the prediction in one line. Then advance one round (`python -m promptpotter resume`; `new` only after a config/prompt change, which mints a fresh `cycle_id`), open `round_(N+1).json`, and compare. If the prediction held, lock it in. If not, classify again — the failure mode was different than thought.

## Edit etiquette (the non-negotiables)

- **One edit per pass.** Multiple simultaneous edits destroy the signal that lets you tell which one helped.
- **You may not add a field to the response contract from the prompt side.** `L1Variant` is `extra="forbid"` and its field set is `dispatch/schemas.py::L1Variant` — read it there. Note `targets_cluster`, which binds a variant to one `l1_critique` root cause: it is the STRUCTURAL answer to semantic restatement, already shipped, so do not re-prescribe a prompt clause for it. A prompt demanding anything else fails **every** variant at validation. Adding one for real means the Pydantic model, both `answer_format`s and `resolved_schemas` move in **one commit**, or the loop stops parsing. Prefer riding `changes_description`.
- **No backward compatibility.** Zero released versions. Change a slot name everywhere — no fallback chains, no defaults. See the STOP section in root `CLAUDE.md`.
- **Slots flow through `DispatchHub`.** New `{{slot}}` names go in `INJECTIONS` (`dispatch/injections/registry.py`); `validate_template` raises at module load on typos. Never summarize a field at the prompt site.
- **L1 owns `pipeline_params`.** If the diagnosis points at the framing surface, write down "→ L2 should refine task_context to X" and stop. That is L2's contract, not yours.
- **Cycle hash awareness.** An optimizer prompt edit changes `JobSearchPoint.content_hash` for the next round but **not** the target cycle's origin hash. At L4 an edit to an **inner** optimizer node's prompt body or config moves `_identity_config`'s `inner_origin` fingerprint and voids banked outer cells; the outer set (`sets/self_optimizing.yaml`), `checkin`, node descriptions and a release do not. See § Why experiments did not accumulate. To keep prior runs comparable, suggest `--fork-on-divergence` after the edit.
- **No hidden defaults.** Render the empty case explicitly rather than "if `axis_memory` is empty, do X".
- **Trim to invariants, not history.** When you remove a line, remove it. No `# was: …` breadcrumbs.

## What L4 does *not* do

- Does not generate new candidates itself. That is L1's job.
- Does not refine `task_context` (L2's) or replan strategy (L3's). L3 firing means the plan-space was wrong, not that the L1 prompt needs tweaking.
- Does not modify `task_description.md` or the per-dataset configs — those change cycle identity and the scoring contract.
- Does not re-score past rounds. If the scoring formula changes, swap it in `campaign.yaml::scoring` and let the next round-end recompile.

## Useful pointers

Paths below are repo-relative; this file sits at `.claude/skills/potter-self/`.

- **L1/L2/L3 agent contracts** — `promptpotter/application/optimization/CLAUDE.md` (what each layer reads, writes and decides).
- **Dispatch hub + info flow** — `docs/developer/dispatch-hub.md`. How slots reach optimizer prompts.
- **The measurand, the invariants, what a panel may claim** — `docs/specs/l4-outer-loop.md`. Read it before trusting any outer number, and before touching a file mid-run.
- **Persistence + the identity fingerprint** — `docs/operations/persistence-and-state.md` (fact 4 owns what `_identity_config` reads).
- **Conventions** — `docs/developer/conventions.md`. Style, no-back-compat, no-hidden-defaults, the reasoning doctrines.

## A short worked example

Operator pauses at round 2: "Why isn't this going anywhere? The candidates all look different."

1. Open `round_0001.json` and `round_0002.json`. Pull every `changes_description` and read them side by side. Four distinct strings — and all four ask the target to reason more carefully before committing to an answer. One hypothesis, four wordings.
2. Check `l1_n_repeat`: 0. Confirms nothing — `idea_fingerprint` is lexical and blind to this.
3. PoBB stream flat, all candidates to `n_min`. Composite ≈ parent. Consistent with four arms testing one idea.
4. Critique agrees ("candidates converge on the same remedy") — so this is L1, not scoring.
5. Classify: **semantic restatement**, the lead entry. Not mode collapse in the mechanical sense; the strings differ.
6. Propose: require `changes_description` to open with the hypothesis under test, and forbid a hypothesis already present in `mutation_memory`. Predict: by round 3 at least two candidates cite *different* evidence panels and name distinct hypotheses.

Re-run one round. Compare against the prediction. If the hypotheses are still one idea, the edit was wrong — reclassify rather than rewording it again, which is the same failure one level up.
