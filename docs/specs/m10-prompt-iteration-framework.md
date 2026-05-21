# M10: Prompt-Iteration Framework + L1-generate Tuning

**Status:** Active.
**Goal:** `rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`.
**Headline metric:** `rounds_to_95` (first round where best accuracy ≥ 0.95; `None` if never).
**Cost envelope:** ≤ ~500 LOC of new code total. No new persistence channel, no new I/O kind. Everything rides existing infra (`AuditTrailView`, `OptSearchPoint`, `MeasurementArchive`, `CycleEventLog.inherit_from`, escalation rules engine, `INJECTIONS`).

This is the M10 charter for everything around tuning the four optimizer meta-prompts: the framework (Tracks 1–4), the unified fork primitive that sweep rides on (Track 5), the sweep toolkit verbs (Track 6), the L2 self-diagnosis surface (Track 7), and Imagination (Track 8 — the deferred bet).

**Sibling mini-milestone:** [`m10-operator-control-loop.md`](m10-operator-control-loop.md) — the webapp single-operator control loop (launch / stop / resume / fork + SSE reactivity + meta-prompt read panel), carved forward from M12 to land alongside these tracks. It is a separate spec with its own `Control-remote` I/O kind and its own cost envelope; this spec's "no new I/O kind" envelope is unaffected.

## Why bother

`l1_generate` is the principal bottleneck — the loop only descends gradient when L1 produces useful variants. L1 has known misbehaviors (ignores `context_object`, mutates LLM-call params before prompt fields saturate) plus open unknowns. Auto-tuning (L4) is too expensive in the small-N regime, so the framework's job is to give the operator + Claude the same quality of feedback an L4 would give itself — manually, fast, and per-cycle.

**L4 partial.** M10 closes most of credit-assignment for a future L4: `proxy_lift_corr` validates the cheap proxy, the unified fork primitive is the cheap-trial mechanism *and* the substrate L4 auto-rebase will plug into, behavior checks are the programmatic conformance signal, `review.md` + `L1Stats` are the per-cycle structured features. PromptPotter-as-backend stays in M12+.

## Operating principles

1. **Each searchpoint is costly.** Round-1 gate is mandatory: halt + review before committing rounds 2–5.
2. **Round-1 sweep, round-2 promote.** Compare N L1 candidates with 1 scored round + 1 unscored generation peek; promote winners to full.
3. **Proxy validation is a first-class question.** If `proxy_lift_corr ≥ 0.6`, sweep-mode is trusted as primary screening. `< 0.4` ⇒ revisit rules. In between ⇒ pair with one confirmation full-cycle.
4. **Build the framework while running.** Each new unknown unknown becomes a new behavior check.
5. **Skill-collaborative analysis is the L4 substitute.** `/potter-l1-meta-campaign` reads cycle artifacts, surfaces the top issue, proposes one edit; operator confirms; Claude edits.
6. **One change per iteration by default.** Bundle only when both edits target the same observed failure and the operator accepts the attribution loss.
7. **General fix, not specific.** Round-3 failure on input X → guard the *class*, re-run.

## Tracks

### Track 1 — L1 behavior checks

Registry at `promptpotter/application/optimization/validators/l1_behavior.py`. Pure functions: `(round_dict, ctx) -> CheckResult`. Adding a check = one-function diff.

| check_id | rule |
|---|---|
| `context_object_honored` | Each variant references ≥1 `context_object` item in `changes_description` or new prompt-field text. |
| `param_scope_discipline` | No variant touches `temperature`/`max_tokens`/`reasoning_effort` while ≥1 prompt field has been unchanged for 2 rounds, or before `param_unlock_round` (default 3). |
| `not_only_param_variants` | ≥1 variant per round mutates a `PROMPT_STRING_FIELDS` field. |
| `evidence_grounding_present` | Each variant's `evidence_grounding.field` ∈ `{parent_panel, sibling_yield, axis_memory, escalation_panel, task_context, plan, stall_exploration}` with non-empty citation. `stall_exploration` only when `escalation_panel.exploration_budget ∈ {normal, wide}`. |
| `optimizer_rewind_guard` *(wired when L2/L3 rebase emission lands — Track 5b/M11)* | If round emitted a `RebaseAction`: target round exists + ≤ current; reason non-empty; payload non-empty. |

`evidence_grounding` is a required field in `l1_generate.json` output schema; carries through `CandidateProposal` → `OptSearchPoint.lineage` → `review.md`'s `evidence` column. Unjustified-mutation count > N triggers the `l2_unjustified_mutations` healing rule (Track 7).

### Track 2 — Per-cycle `review.md`

Pure renderer at `promptpotter/application/review.py` (peer of `presentation/views/render/markdown.py`).

```python
def render_review_md(
    index: dict[str, Any],
    rounds: list[dict[str, Any]],
    *,
    prompt_snapshots: dict[str, dict[str, Any]] | None = None,
) -> str: ...
```

**Per-round section:** L1 inputs (`task_context`, fed critique, three `context_object` items) · behavior-check checklist (✓/✗ + evidence) · variants table (`variant | composite | accuracy | Δ_parent | Δ_origin | beat_parent | evidence | changes`) · this round's critique · **next-generation peek** (sweep mode only — the `OptSearchPoint` traces L1 produced for the halted next round, rendered as `cand_id | changes | derived_axes`, no fitness columns).

**Header:** four prompt-template hashes · `L1Stats` block · behavior-violation summary · **round-1 verdict** (Track 3).

Wired into `runner/entry.py::_finalize_run` AND emitted incrementally after round 1 so the round-1 gate has something to read. `tests/test_invariants.py::PER_CYCLE_OPERATOR_ARTIFACTS` gains `review.md`.

### Track 3 — `L1Stats`

Frozen dataclass returned by `compute_l1_stats(rounds, *, origin_composite, behavior_results) -> L1Stats`.

| metric | formula | role |
|---|---|---|
| `rounds_to_95` | first round where best accuracy ≥ 0.95; `None` if never | **headline** |
| `round_1_verdict` | `healthy` / `degraded` / `broken` per rule table below | **gate signal** |
| `yield_rate` | mean over rounds of (variants beating parent / variants generated) | diagnostic |
| `top_lift_mean` | mean over rounds of (best variant composite − parent composite) | diagnostic |
| `behavior_pass_rate` | (round × check) cells passing / total | diagnostic |
| `stagnation_max` | longest run with `top_lift ≤ 0` | diagnostic |
| `l2_fires` | rounds with `lineage.source == l2_context` | diagnostic |
| `proxy_lift_corr` *(cross-cycle, computed by leaderboard)* | Spearman rank corr between round-1 `top_lift` and final outcome across qualifying cycles | **proxy validity** |

Parent composite: `origin_composite` for round 1, `trials[r-1].composite` thereafter.

**Round-1 verdict** is conformance-anchored — it keys off behaviour-check conformance alone. `yield_rate` and `top_lift_mean` are confounded by dataset headroom (a capacity-bound dataset cannot register a gain) so they ride `L1Stats` as diagnostics, never as verdict inputs: zero conformance ✗ → `healthy` · exactly one ✗ → `degraded` · ≥ 2 ✗ (or a persistent `forbidden_axes_honored` violation) → `broken`. A healed `forbidden_axes_honored` ✗ does not count. Accuracy validity is checked periodically via `conformance_lift_corr` (Spearman of conformance against accuracy lift) on a movable dataset, not per cycle.

### Track 4 — Cross-cycle / cross-branch leaderboard

Read-only at `promptpotter/application/leaderboard.py`; shim `scripts/ppot_review.py`. Stdout-only. Not a new write verb.

Row: `branch_path | dataset | pipeline | l1_generate_hash[:8] | l1_critique_hash[:8] | mode | fork_trigger | rounds_to_95 | round_1_verdict | round_1_top_lift | round_1_yield | best_acc | origin_acc | Δacc | behavior_pass_rate | rounds_completed | l2_fires | stop_reason`.

`branch_path` = `root_cycle_id → … → leaf_cycle_id`. `fork_trigger` ∈ `{none, operator_sweep, operator_rewind, l2_rebase, l3_rebase, scoring_divergence}`. `mode` ∈ `{sweep, full}`.

**Views.** `--leaderboard` sorts everything `(l1_generate_hash, rounds_to_95 asc with None last, behavior_pass_rate desc)`. `--sweep` filters to `fork_trigger = operator_sweep`, groups by parent root, sorts by `round_1_top_lift` desc — that's the narrowing view for picking sweep winners. Footer reports `proxy_lift_corr` across branches with the same `l1_generate_hash` in both modes.

### Track 5 — Unified fork primitive

Today `DecisionRecord(kind=FORK_CUT)` is scoring-divergence-only. Generalize so **any** trigger emits the same shape: sweep, operator rewind, L2 rebase, L3 rebase, M12 pipeline-switch, M11 webapp-replay, future L4 auto-rebase. One mechanism, multiple drivers.

```python
class ForkTrigger(StrEnum):
    OPERATOR_SWEEP    = "operator_sweep"
    OPERATOR_REWIND   = "operator_rewind"       # M11 — labelled manual fork
    L2_REBASE         = "l2_rebase"              # M11 — gated on --allow-llm-rebase
    L3_REBASE         = "l3_rebase"              # M11 — gated on --allow-llm-rebase
    SCORING_DIVERGENCE = "scoring_divergence"   # already wired
    # M12+ enum additions only — payload + mechanism unchanged:
    # PIPELINE_SWITCH, COMPETITOR_HARNESS, WEBAPP_REPLAY, L4_AUTO_REBASE

class ForkPayload(BaseModel):
    """The diff that distinguishes the fork from its parent at the cut.
    Trigger-agnostic — every trigger uses this shape."""
    trigger: ForkTrigger
    reason: str                          # mandatory; LLM-issued or operator label
    issued_by: str                       # operator id / "L2" / "L3" / "L4" / "system"
    brief: str | None = None
    l1_section_overrides: dict[str, bool] | None = None
    l1_section_overrides_text: dict[str, str] | None = None
    l1_template_override: str | None = None
    pipeline_swap: dict | None = None    # M12 connector / pipeline change at cut
    scoring_swap: dict | None = None     # explicit scorer change at fork
```

`DecisionRecord(kind=FORK_CUT)` keeps `inputs_ref={"from_round": N}` + `outcome=new_cycle_id`; the payload moves into `data.fork: ForkPayload`. Scoring-divergence forks keep working — runner backfills `trigger=SCORING_DIVERGENCE`, `issued_by="system"`, `reason="scorer_mismatch:<decision_kind>"`. `FORK_CUT` stays `ARCHIVAL` in `DECISION_GATING`.

**Mechanism.** Rename `_fork_at_divergence()` → `_mint_fork(parent, fork_from_round, payload)` in `application/optimization/cycle.py`. Body unchanged: parent ledger gets `FORK_CUT` with `data.fork`, fork dir under `campaigns/{root}/forks/{cycle_id}/`, rounds/candidates copied for rounds `< fork_from_round`, active pointer retargeted, `CycleEventLog.inherit_from(parent, parent.next_offset)`.

**LLM-side surface.** Extend `OptimizerAction` with `rebase: RebaseAction | None` (ForkPayload-shaped sans `trigger`/`issued_by`; runner stamps those from the emitting layer). **L1 cannot rebase** — only outer layers (L2/L3). L1 is the thing being rebased on. M10 ships the schema field unwired; M11 wires emission.

**Telemetry.** Active fork's `cycle_id` already at `dashboard.json::cycle_id`; extend with `cycle_id_path: list[str]` (root → … → current) so live view + webapp render the tree without walking ledgers.

### Track 6 — Sweep toolkit

Four verbs + one view. Not a workflow — tools you jump between. Result JSONs persist under `archive/sweeps/{l1_meta_prompt_hash}/{dataset}/{verb}_{timestamp}.json`; `rank` reads them.

| verb | answers |
|---|---|
| `time-to N` | How many rounds / how much spend until L1-vX hits N% on dataset D? Halts on accuracy / max-rounds / max-spend. |
| `round1` | One round on a panel of L1 variants. Per-variant `round1_accuracy`, `round1_best`, `panel_size`, `parse_fail_rate`, `pipeline_params_entropy`, `cost_usd`. |
| `round2` | Reads a prior `round1` sweep, takes top-K survivors, runs one more round. Adds `round2_accuracy`, `round2_lift`, `cumulative_cost_usd`. |
| `slice` | Modifier on any verb: `hard` (top quartile by `SampleProfile` difficulty), `easy` (bottom), `all` (default), `samples=S1,S2,…`. Free variance reduction. |
| `rank` | Reads last N sweep JSONs for a dataset, prints a sorted table. Columns: every verb-emitted field + derived `cost_per_lift`. Pure read; no persistence. |

**Result JSON** — single shape across verbs, some fields populated per verb: `{sweep_id, verb, timestamp, l1_meta_prompt_hash, l1_meta_prompt_label, dataset, slice, panel_size, round1_accuracy, round1_best, round2_accuracy, round2_lift, rounds_to_target, early_exit_reason, parse_fail_rate, pipeline_params_entropy, diversity_l2_score, cost_usd, final_accuracy, notes}`.

**No held-out eval set, no replay corpus, no projection layer.** Cheap models make live sweeps cheap enough that frozen 30-sample mini-benchmarks are overbuilt; cross-dataset signal comes from running sweeps on different datasets. `diversity_l2_score` placeholder is for a later L2-rates-diversity follow-on (L2 already sees the panel; one new field in its response schema, no new LLM call).

### Track 7 — L2 self-diagnosis surface

L2 today reads (via `INJECTIONS`): `parent_panel`, `sibling_yield`, `axis_memory`, `escalation_panel`, `task_context`, `l1_critique`, `prev_l2_directive`, `l1_signal_catalogue`. To diagnose L1 instead of just framing edits, it needs four more panels and one escalation signal:

- **`l1_considered_mutations`** — per-round trace of what L1 *proposed*, not just what won. From `CandidateScore` in `RoundResult.candidate_scores`. Renders `cand_id | mutation | evidence_field | composite | beat_parent`. Separates "bad pool" from "bad selection."
- **`axis_exhaustion`** — promote `AxisIndex.digest()` exhaustion-per-axis to an explicit boolean (`exhausted = n_tried ≥ N and mean_delta within noise_floor`); surface prominently.
- **`sample_delta`** — top-K (default 5) regressions + top-K gains, sorted by `|delta|`. Lets L2 cite specific samples.
- **`l1_verbosity_stats`** — chars per prompt field for parent + winner + each scored candidate vs. configured soft thresholds (`campaign.json::optimization.prompt_field_char_thresholds`, defaults `{persona: 600, task_intent: 800, problem_description: 1200, thinking_style: 800, system_role: 400, output_format: 400, examples: 1500, constraints: 600}`).

Four new entries in `INJECTIONS`, four new lines in `l2_context.md`. All derived from `RoundResult` / `AxisIndex` / `SampleIndex` / `cycle.opt_sp`. **Vocabulary contract:** L2 must be the writer of these reads — every new signal earns its place by being a vocabulary item L2 cites in `task_context` deltas.

**Verbosity self-healer (weak notice).** Low-priority escalation rule firing when verbosity stats from `l1_verbosity_stats` cross threshold, regardless of L1 stall:

```python
EscalationRule(
    name="prompt_field_above_verbosity_threshold",
    when=lambda s: s.over_threshold_field_count > 0,
    fire=NextAction.CONTINUE,    # never preempts a real escalation
    priority=5,                   # below l1_to_l2 (10)
    reason=lambda s: f"{s.over_threshold_field_count} prompt field(s) over verbosity threshold",
)
```

`EscalationInputs` gains `over_threshold_field_count: int = 0`, computed at `EscalationState.observe_round` from `cycle.opt_sp.prompt_field_dict()` + thresholds. Fires `CONTINUE` — surfaces signal only; one `escalation/rule_fired` PhaseRecord per fire. Composite-score *penalty* for verbosity is M12 (`m12-multi-connector.md`).

### Track 8 — L2 Imagination *(deferred — §0 amendment)*

The structural bet. Only fires after Tracks 1–7 are live and we've measured whether the existing surface plus better panels closes the gap.

**Shape.** New optimizer prompt `l2_imagine` invoked by L2 *before* it commits a `task_context` refinement. L2 produces 2–3 *candidate framings* (A, B, C); Imagination simulates, for each, the predicted L1 mutation set; the highest-scoring framing wins.

**Cost.** §0's four-LLM-calls invariant becomes **five** — explicit `CLAUDE.md` + `promptpotter/CLAUDE.md` amendment required, lands as a separate PR before this code. ~250 LOC: one new prompt entry, one transition pre-step, new `ResumeCheckpointKind.L2_IMAGINE`, new `INJECTIONS` entry `l2_candidate_framings: list[dict]`, `_TEMPLATE_EXTRAS["l2_imagine"] = {"n_framings"}`.

**Gating.** Fires only when L2 is about to fire anyway (L1 stall hit patience) AND `axis_memory` has ≥5 rounds of digest history AND `campaign.json::optimization.l2_imagination = True` (default off). Gated off → existing `task_context` refinement runs unchanged.

**Scoring of rollouts.**
1. **Proxy (v0).** Score predicted mutations against `AxisIndex.digest()` — sum each axis's historical `mean_delta` across the predicted set; pick the highest sum. Free; requires ≥~5 rounds of history.
2. **Mini-eval rollouts (fallback).** Run each predicted variant on 3–5 samples; pick framing whose set scored best. ~3× backend cost; works from round 1.

Ship proxy only. Mini-eval is a follow-up if `imagination_lift_corr < 0.5` over the first 10 imagine fires.

### Track 9 — Methodology doc

`docs/methods/manual-prompt-tuning.md`: goal restatement · operating principles · sweep workflow · single-cycle cadence · round-1 verdict rule table · diagnosis tree (behavior ✗ → fix the class · all ✓ + low yield → broaden L1 creativity · all ✓ + flat lift → scoring/sample-set issue · early `l2_fires` → `l1_critique` weak) · bundling carve-out · proxy validation procedure · generalization gate (re-run on second pipeline before promoting) · adding a new behavior check · stopping criteria.

Sibling: `docs/operations/operator-loop.md` (round stats per round, improvement velocity, the spend loop: define → compute → review → redefine).

## Wave sequencing

Biggest-blocker first — Track 5a (the fork primitive) gates the breadth-first sweep workflow on day one.

1. **5a — fork primitive (M10).** Domain extension + `_mint_fork()` generalization + scoring-divergence retrofit + `new --sweep-batch` as first caller. `OptimizerAction.rebase` schema added but unwired.
2. **7 verbosity rule (~30 LOC).** Pure observability, lowest risk.
3. **1 evidence-grounding validator (~80 LOC).** Schema field additive; validator heal-able not fatal.
4. **6 sweep toolkit verbs** — incremental: `time-to` day 1, `round1` day 2, `round2` day 3, `slice` day 4, `rank` day 5. `rank` slipping a day is fine — the four verbs are usable without it.
5. **1+3 parallel** — L1 behavior checks + `L1Stats` against sweep round-1 data as it accumulates.
6. **4 leaderboard.** Consumes 5a fork metadata + Track 3 stats.
7. **2 `review.md`.** Per-fork view. Runner wiring + parity test + incremental round-1 emission.
8. **7 panel additions (~150 LOC).** L2 self-diagnosis reads.
9. **9 methodology doc** + `l1_generate.json` revision.
10. **5b — LLM-rebase callers (M11).** L2/L3 rebase emission + patience gating + `--allow-llm-rebase` opt-in + `optimizer_rewind_guard` behavior check. Out of M10.
11. **8 Imagination.** Only after 1–7 are live and we've measured the gap.
12. **Iterate.** Each new unknown unknown → new behavior check + skill rule update; each batch of sweep branches updates `proxy_lift_corr`.

## Exit gate

- All deliverables shipped, tests green.
- `rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`.
- `behavior_pass_rate = 1.0` for both seeded checks across qualifying cycles.
- `/potter-l1-meta-campaign` catches at least one prompt regression on a re-run before round 2 fires.
- **Proxy hypothesis decision recorded** — either `proxy_lift_corr ≥ 0.6` over ≥4 paired branches (sweep validated), or rules modified per the proxy validation procedure with rationale in the methodology doc.
- Methodology doc maps section-by-section to actual `review.md` output.
- **Unified `_mint_fork()` covers both scoring-divergence and operator-sweep callers under one code path.** `DecisionRecord(kind=FORK_CUT).data.fork: ForkPayload` populated for both. `OptimizerAction.rebase` schema field present (unwired in M10; M11 wires emission).

## Out of scope

- Auto-tuning prompts (L4) → [`m12-plus-backlog.md`](m12-plus-backlog.md).
- Test-set validation → M11.
- Third pipeline → M12 multi-connector.
- Webapp surfacing → M11/M12.
- Composite-score verbosity penalty → [`m12-multi-connector.md#track-5--composite-fitness-function`](m12-multi-connector.md#track-5--composite-fitness-function).
- Variant-vs-sample heatmap in `review.md`; mechanical diversity-as-constraint (L2 rates diversity subjectively as a follow-on).
- L1 meta-prompt decomposition (splitting overloaded `l1_generate` into sub-prompts) — separate spec, after the toolkit ships.

## Open decisions

- `param_unlock_round` default: 3.
- Round-1 verdict thresholds: calibrate after first 3 cycles.
- Evidence strictness: substring match; upgrade to semantic only if false-pass rate misleads iteration.
- Sweep-payload source: `datasets/{name}/sweep/*.json` (one `ForkPayload` per file) for git-tracked reproducibility.
- L2/L3 rebase patience defaults (5b): proposed `l2_rebase_patience = l2_patience × 2`, `l3_rebase_patience = l3_patience × 2`. Calibrate during M11.
- `cycle_id_path` exposure: `dashboard.json` only, or also `index.json::final`? Default to both — cheap, makes webapp wiring trivial.

## Key existing code

| Area | Files |
|---|---|
| Optimizer prompts | `application/optimization/optimizer_pipeline.json::resolved_prompts['{l1_generate,l1_critique,l2_context,l3_plan,restructure}/1']` |
| Optimizer output schemas | `optimizer_pipeline.json::resolved_schemas['{l1_generate,l1_critique}/1']` (`l1_generate` is the static envelope; `build_l1_output_schema` grafts per-target node properties on top) |
| Prompt loading | `pipeline.py::load_optimizer_prompt` |
| Per-round trace | `campaigns/{cycle_id}/.cache/rounds/round_NNNN.json` (`nodes.l1_generate`, `nodes.l1_score`, `nodes.l1_critique`) |
| Per-round optimizer state | `campaigns/{cycle_id}/rounds/round_NNNN.json` |
| Prompt snapshots | `campaigns/{cycle_id}/prompts/optimizer_prompt/{hash}/` |
| Existing log renderer | `presentation/views/render/markdown.py::to_markdown` (purity pattern) |
| Reusable formatters | `presentation/views/display.py` |
| Surface compile path | `application/optimization/pipeline.py::compile_l1_surface`, `compile_l2_surface`, `compile_l1_critique_blob` |
| Cycle finalize / round emission | `application/runner/` |
| Operator skills | `.claude/skills/potter-run/SKILL.md`, `.claude/skills/potter-l1-meta-campaign/SKILL.md` |
| Parity test | `tests/test_invariants.py::PER_CYCLE_OPERATOR_ARTIFACTS` |
| **Fork substrate** | `domain/run_records.py::DecisionKind.FORK_CUT` + `DECISION_GATING`; `infrastructure/ledger.py::CycleEventLog.inherit_from`; `application/optimization/cycle.py::_fork_at_divergence` (rename → `_mint_fork`); `application/runner/entry.py::fork_on_divergence` plumbing; `presentation/cli/commands/resume.py::--fork-on-divergence`; `presentation/api/` ForksResponse |
| **Active-pointer + family-root binding** | `infrastructure/store/stores.py::save_active_pointer`; `infrastructure/projections/live_dashboard/view.py` (telemetry binds to root with no `parent_cycle_id`) |

## Risks

| risk | mitigation |
|---|---|
| Proxy hypothesis fails | Detected by `proxy_lift_corr`; framework modifies on the go per the proxy validation procedure. |
| Sweep mode hides regressions that only surface round 2–3 | Pair every promoted candidate with a full-cycle confirmation; never publish a prompt with only sweep-mode evidence. |
| Round-1 gate triggers on noise | Operator can override; skill warns but doesn't block. Calibrate after first 3 cycles. |
| Substring match too coarse (L1 superficially echoes a token) | Upgrade to semantic only if false-pass rate misleads iteration. |
| Behavior-check registry grows ad-hoc, rules contradict | Methodology doc owns the running list; consolidate periodically. |
| `l1_generate.json` over-restricted, yield drops | One knob per iteration + general-fix rule keep edits reversible; leaderboard catches regression. |
| 95% unreachable on TermNorm under current pipeline | Track per-pipeline `rounds_to_95` separately; recalibrate at exit-gate review. |
| Skill proposes wrong fix, operator follows blindly | Methodology doc forbids "apply without thinking"; operator confirms every edit. |
