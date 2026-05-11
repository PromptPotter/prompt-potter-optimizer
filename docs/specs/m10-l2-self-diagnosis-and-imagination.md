# M10 (sub-spec): L2 Self-Diagnosis + Imagination

**Version:** 0.1.0
**Date:** 2026-05-11
**Status:** Spec — code blocked on Track 4 §0 amendment + operator review
**Depends on:** [`m10-prompt-iteration-framework.md`](m10-prompt-iteration-framework.md)
**Cross-ref:** [`m12-composite-fitness.md`](m12-composite-fitness.md) (multi-objective penalty; verbosity composite term lives there), [`m11-spend-tracking.md`](m11-spend-tracking.md) (operator-loop doc surface)

---

## Goal

Close the L2 self-diagnosis surface so the L2-layer can decide *why* L1 stalled without operator hand-holding. Add an opt-in **Imagination** rollout step so L2 picks task-context revisions based on predicted L1 behavior rather than blind framing edits. Surface a **weak verbosity self-healer** signal (notice-only; composite-penalty piece is M12).

Three failure modes today justify the work:

1. **L1 gambles when it has no evidence** despite `promptpotter/CLAUDE.md` saying *"No data justifying a choice ⇒ do not gamble."* L1 has the contract but no validator — there is no check that the candidate cited a panel field.
2. **L2 can't tell why L1 stalled** because it sees the chosen variant but not the rejected alternatives nor the per-axis exhaustion. Critique text fills the gap unreliably.
3. **L2's framing edits are speculative** — the existing `l2_directive` is a write-forward action. There is no read-forward step to compare framing A vs framing B before committing.

## Scope boundary

In scope: L1 evidence-grounding validator, panel additions on L2's surface (option-set + axis exhaustion + sample delta + verbosity stats), opt-in `l2_imagine` LLM call (§0 amendment), verbosity escalation rule (notice-only).

Out of scope: composite-score verbosity penalty (lives in `m12-composite-fitness.md`); operator-loop docs (lives in `m11-spend-tracking.md` + new `docs/operations/operator-loop.md`).

## Tracks

### Track 1 — L1 evidence-grounding validator (small win)

L1 produces variants with no obligation to cite the panel field justifying the mutation. The contract says it must; nothing enforces it.

**Schema change.** `l1_generate.json` output schema gains a required `evidence_grounding` field per variant: `{field: "parent_baseline" | "sibling_yield" | "axis_memory" | "escalation_panel" | "task_context" | "plan" | "stall_exploration", citation: str}`. `stall_exploration` is the explicit-stall escape hatch (the contract's *"random exploration is reserved for explicit stall"* clause) — only valid when `escalation_panel.exploration_budget ∈ {normal, wide}`.

**Validator.** New check in `promptpotter/application/optimization/l1_behavior_checks.py`:

| check_id | rule |
|---|---|
| `evidence_grounding_present` | Each variant has non-empty `evidence_grounding.field` AND `evidence_grounding.citation`; `stall_exploration` only when `exploration_budget != "tight"`. |

**Wiring.** `CandidateProposal` gains an `evidence_grounding: EvidenceGrounding` field (frozen Pydantic). `OptSearchPoint.lineage` carries it forward so audit-trail rounds expose it. `review.md` per-round variants table gains an `evidence` column.

**Healing.** Unjustified-mutation count > N triggers `l2_unjustified_mutations` rule (Track 4) — L2 heals by clamping `exploration_budget` and tightening L1 framing.

**Cost:** ~80 LOC. Schema + validator + lineage carry. No new persistence, no new LLM call.

### Track 2 — L2 self-diagnosis panel additions

L2 today reads (via `INJECTIONS`): `parent_baseline`, `sibling_yield`, `axis_memory`, `escalation_panel`, `task_context`, `l1_critique`, `prev_l2_directive`, `l1_signal_catalogue`. To diagnose L1, it needs four more:

#### 2.1 `l1_considered_mutations`

Per-round trace of what L1 *proposed*, not just what won. Sourced from `CandidateScore` already in `RoundResult.candidate_scores`. Renderer: `cand_id | mutation | evidence_field | composite | beat_parent`.

Today L2 reads only the winner via `parent_baseline` + `sibling_yield`. Adding the loser pool lets L2 separate "bad candidate pool" (all candidates regressed) from "bad selection" (one candidate beat parent but wasn't picked — bug).

#### 2.2 `axis_exhaustion`

`AxisIndex.digest()` already carries effect sizes vs noise floor; promote an explicit boolean per axis: `exhausted = (n_tried >= N and mean_delta within noise_floor)`. `axis_memory` already lists exhausted axes per its docstring; verify the rendering surfaces them prominently rather than burying them in the rankings table.

#### 2.3 `sample_delta`

Per-round per-sample regression/gain view. `SampleIndex` already tracks per-sample state; expose `regressions: list[{sample_id, parent_score, winner_score}]` + `gains: list[same]` to L2. Lets L2 cite specific samples in `task_context` refinements.

Renderer: top-K (default 5) regressions + top-K gains, sorted by `|delta|`.

#### 2.4 `l1_verbosity_stats`

Chars per prompt field for parent + winner + each scored candidate, vs. configured soft thresholds (`campaign.json::optimization.prompt_field_char_thresholds`, default `{persona: 600, task_intent: 800, problem_description: 1200, thinking_style: 800, system_role: 400, output_format: 400, examples: 1500, constraints: 600}`).

Renderer: `field | parent_chars | winner_chars | threshold | over?`. L2 reads this when deciding whether to issue a verbosity-trim directive.

**Wiring.** Four new entries in `dispatch_hub.INJECTIONS`; four new lines in the L2 template (`l2_context.md`). All four are derived signals — built from existing `RoundResult` / `AxisIndex` / `SampleIndex` / `cycle.opt_sp`. No new persistence.

**Cost:** ~150 LOC across `dispatch_hub.py` + `optimizer_pipeline.json` template + small renderers. No new LLM call. Per-track 1 contract: L2 must be the writer of these reads — every new signal earns its place by being a vocabulary item L2 cites in `task_context` deltas.

### Track 3 — Verbosity self-healer (weak notice)

Add a low-priority escalation rule that fires when verbosity stats from Track 2.4 cross threshold, regardless of L1 stall:

```python
EscalationRule(
    name="prompt_field_above_verbosity_threshold",
    when=lambda s: s.over_threshold_field_count > 0,
    fire=NextAction.CONTINUE,   # never preempts a real escalation
    priority=5,                  # below l1_to_l2 (10)
    reason=lambda s: f"{s.over_threshold_field_count} prompt field(s) over verbosity threshold",
)
```

Requires extending `EscalationInputs` with `over_threshold_field_count: int = 0` and computing it at observation time (`EscalationState.observe_round` reads `cycle.opt_sp.prompt_field_dict()` + thresholds).

Rule fires `CONTINUE` — it never stops or escalates. Effect: one line per fire in `.runtime/signals.jsonl` (via `SignalsProjection`) + a `recent_rules` entry on the dashboard. L2 reads `current_signals` next round and may issue a trim directive on its own initiative.

Composite-score *penalty* for verbosity is M12 (`m12-composite-fitness.md`). This rule only surfaces the signal; weighting it into the fitness function is a separate, harder decision (per-objective weights, Pareto front).

**Cost:** ~30 LOC. One rule + one `EscalationInputs` field + one threshold-config wiring.

### Track 4 — L2 Imagination (§0 amendment)

The structural addition. **Blocks on §0 amendment + operator review** — adds a fifth LLM-call kind beyond `l1_generate`, `l1_critique`, `l2_context`, `l3_plan`.

**Shape.** New optimizer prompt `l2_imagine` invoked by L2 *before* it commits a `task_context` refinement. L2 produces 2-3 *candidate framings* (call them A, B, C); the Imagination call simulates, for each, the *predicted L1 mutation set* (cand list + evidence-grounding per cand). The Imagination output is scored against `axis_memory` digest (proxy: predicted-mutation hit rate on historically-effective axes) and L2 picks the highest-scoring framing.

**Architecture changes.**

- §0 four-LLM-calls invariant in `CLAUDE.md` becomes **five** — explicit amendment required, this PR cannot land before §0 updates.
- New `INJECTIONS` entry: `l2_candidate_framings: list[dict]` (input to imagine).
- New template `l2_imagine.md` + `_TEMPLATE_EXTRAS["l2_imagine"] = {"n_framings"}`.
- New entry in `optimizer_pipeline.json::resolved_prompts`.
- `transitions.py::transition_l2` gains a pre-step that calls `l2_imagine` when `campaign.json::optimization.l2_imagination = True` (default off).
- New `ResumeCheckpointKind.L2_IMAGINE` for replay gating.

**Scoring of rollouts.** Two options:

1. **Proxy scoring (recommended for v0).** Score predicted mutations against `AxisIndex.digest()` — for each predicted mutation, look up the axis's historical mean_delta; sum across the predicted set; pick framing with highest sum. ~free; works only if AxisIndex has signal (≥ ~5 rounds of history).
2. **Mini-eval rollouts.** Run each predicted variant on a tiny sample subset (e.g. 3-5 samples) and pick framing whose predicted set scored best. ~3× backend cost; works from round 1.

v0 ships proxy only. Mini-eval is a follow-up if proxy `imagination_lift_corr < 0.5` over the first 10 imagine fires.

**Gating.** Imagination only fires when:
- L2 is about to fire anyway (L1 stall hit patience), AND
- `axis_memory` has ≥ 5 rounds of digest history (proxy scoring is meaningful), AND
- `campaign.json::optimization.l2_imagination = True`.

When gated off, L2's existing `task_context` refinement runs unchanged.

**Cost:** ~250 LOC. One new prompt, one new transition step, one new ledger record kind. Plus §0 doc update (small but load-bearing).

## Architectural impact

- **Tracks 1-3 are extensions, not new invariants.** They extend existing surfaces (L1 schema, L2 panel, escalation rule set). No §0 amendment, no new I/O kind, no new persistence channel.
- **Track 4 amends §0.** The "four optimizer LLM calls go through one path" invariant in root `CLAUDE.md` + `promptpotter/CLAUDE.md` becomes five. This is the largest cost in the spec; everything else is in-frame.
- **No new I/O kind.** All four tracks ride existing channels: `CycleEventLog.append` (persistence), ledger subscribers (display), `Session.stop_check` (control). Imagination's intermediate state lives in `OptSearchPoint.task_context_candidates: list[TaskDecomposition]` — same channel as existing `task_context`.

## Pre-flight gate (per root `CLAUDE.md`)

1. **§0 bucket:** Tracks 1-3 = central loop + dispatch. Track 4 = central loop + dispatch + **§0 itself**.
2. **Existing channel does this:** Track 1 = no (no evidence validator today). Track 2 = partial (digests exist, surfacing varies). Track 3 = yes (escalation rules engine). Track 4 = no (no read-forward LLM call).
3. **Name distinct:** `evidence_grounding`, `l1_considered_mutations`, `axis_exhaustion`, `sample_delta`, `l1_verbosity_stats`, `l2_imagine` — all greppable, all single-use.
4. **Self-describing:** `l2_imagine` is the only name that could read as ambiguous; documented inline as "L2's read-forward rollout call" wherever it appears.
5. **Rides existing infra:** Tracks 1-3 yes. Track 4 needs one new `ResumeCheckpointKind` + one new prompt entry — minimal sidecar.
6. **AI-accessible on disk:** All four tracks land facts on disk: variant evidence in `round_NNNN.json`, exhaustion + sample delta in derived projections, verbosity firings in `signals.jsonl`, imagine inputs/outputs in `LLMCallRecord` + `round_NNNN.json`.
7. **§0 update:** Track 4 yes (separate PR, lands first). Tracks 1-3 no.
8. **Langfuse trace:** Track 4 LLM call wraps with `observed_node("l2_imagine_r{N}", "llm/meta", ...)`.

## Sequencing

1. **Track 3 first** (verbosity rule, ~30 LOC) — pure observability, no behavioral change, lowest risk.
2. **Track 1** (evidence-grounding validator, ~80 LOC) — gates L1's contract violation; small risk because the schema field is additive and the validator is heal-able rather than fatal.
3. **Track 2** (panel additions, ~150 LOC) — read-only on L2's side; new template slots; minor risk of payload size growth in L2 prompt.
4. **Track 4** (Imagination, ~250 LOC + §0 amendment) — only after Tracks 1-3 are live and we've measured whether the existing surface plus better panels closes the gap. Imagination is the bet; the bet should be made on evidence that the cheaper tracks didn't suffice.

## Validation

Each track has a measurement gate before the next lands.

| Track | Gate | Pass criterion |
|---|---|---|
| 3 | First 3 cycles after landing | Verbosity rule fires on ≥ 1 cycle; signal correlates with operator's manual verdict (subjective) |
| 1 | First 3 cycles | `unjustified_mutation_rate < 20%` on round 1; L2 cites `evidence_grounding` field in ≥ 1 `task_context` delta |
| 2 | First 3 cycles after Track 1 | L2 `task_context` deltas cite at least one of `regressions[]`, `exhausted_axes`, `over_threshold_field` per fire (sampled) |
| 4 | First 10 imagine fires | `imagination_lift_corr ≥ 0.5` against post-hoc actual lift; if not, mini-eval fallback (Option 2 above) |

## What this is not

- **Not an L4 substitute.** L4 self-optimization (per `m12-plus-backlog.md`) requires PromptPotter-as-backend; this spec ships better diagnostic signals so the *operator* can iterate faster (per M10's "skill-collaborative analysis" principle).
- **Not a composite-score change.** Verbosity penalty in the fitness function is M12.
- **Not a new I/O kind.** Imagination's intermediate state rides existing `OptSearchPoint.task_context_candidates` extension; no new persistence channel.

## Operator loop doc (sibling deliverable)

A new `docs/operations/operator-loop.md` lands alongside this spec covering:

- Round stats per round (already in `dashboard.json`).
- Improvement velocity over last N rounds (new derived view).
- The human spend loop: define spend → compute → review (`review.md` + dashboard) → redefine spend → next cycle.

Doc-only; no code in this spec. See `m11-spend-tracking.md` for the spend-side surfaces.
