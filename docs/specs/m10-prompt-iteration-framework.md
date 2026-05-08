# M10: Prompt-Iteration Framework + L1-generate Tuning

**Version:** 0.2.0
**Date:** 2026-04-30
**Status:** Planned
**Depends on:** M9

**0.2.0 amendment:** Track 5 replaces the original `optimize --sweep` halt-flag with a **unified fork primitive** (typed `ForkPayload`, trigger-agnostic `_mint_fork()`). Sweep becomes the first caller; future L2/L3 rebase, L4 auto-rebase, M12 pipeline-switch, and M11 webapp-replay are additional callers built on the same mechanism. See Track 5.

---

## Goal

≥95% training-set accuracy in ≤5 rounds on `llm_only` AND TermNorm under the same prompt revision. ~1 week of manual iteration once framework lands. Then M11 picks up for test-set + benchmarks.

## L4 partial — what this also is

The framework doubles as the partial implementation of L4 self-optimization (see [`m12-plus-backlog.md § Self-optimization`](m12-plus-backlog.md)). L4 has two blockers: (1) credit assignment / cheap proxy reward; (2) "PromptPotter-as-backend" adapter. **M10 closes most of (1)**: `proxy_lift_corr` is the credit-assignment validation, the unified fork primitive (Track 5) is the cheap-trial mechanism *and* the substrate that L4 auto-rebase will plug into, behavior checks are the programmatic conformance signal, `review.md` + `L1Stats` are the per-cycle structured features. (2) stays in M12+. Architectural consequence: **target ≤ ~500 LOC of new code** (50 LOC bump from 0.1.0 to absorb the fork primitive generalization), all reusing existing primitives (`AuditTrailProjection`, `OptSearchPoint` traces, `MeasurementArchive`, `CycleLedger.inherit_from`, `formatting.py`, scipy.stats). No new persistence, no new abstraction layer. If a proposed component doesn't forerun L4, push back.

## Why a milestone

`l1_generate` is the principal bottleneck — the loop only descends gradient when L1 produces useful variants. L1 has known misbehaviors (ignores `context_object`, mutates LLM-call params before prompt fields are saturated) plus open unknown unknowns. **Auto-tuning ("L4") is too expensive in the small-N regime — no replay surrogate, no large run dataset to fit a meta-policy on.** The framework's job is to give the operator + Claude the same quality of feedback an L4 would give itself, manually, fast.

Lifted from M9 Track 1: infrastructure + prompt-tuning don't share work-units. Tuning before benchmarking is cheaper than re-running benchmarks against an untuned loop.

## Operating principles

1. **Each searchpoint is costly.** Treat rounds as scarce. **Round-1 gate (mandatory):** after round 1, halt and review before committing rounds 2-5. Most diagnostic signal is in round 1; rounds 2-5 only earn their cost if round 1 looks healthy.
2. **Round-1 sweep, round-2 promote.** When comparing N candidate L1 prompts, run each for **1 scored round + 1 unscored generation peek** (round 2's L1 generates variants but does not score them — their proposals are persisted as `OptSearchPoint` traces only). Compare side-by-side cheaply. Promote winners to a full round 2 scoring. Spin up new candidates from learnings; same protocol.
3. **Proxy validation is a first-class question.** The above protocol assumes round-1 stats predict full-cycle outcome. **Validate or refute that hypothesis as the data accumulates.** If the proxy holds, cost drops ~80%; if it doesn't, the framework gets modified on the go (sweep-mode rules update, not the whole stack).
4. **Build the framework before and while running.** The framework lands first (Tracks 1-4). Subsequent `optimize` runs feed it. Each new "unknown unknown" surfaced in a run becomes a new behavior check — the registry grows with the iteration, not before it.
5. **Skill-collaborative analysis, not solo runs.** Operator runs `optimize`, then triggers a skill that reads the cycle's artifacts, surfaces the top issue, and proposes one fix. Operator confirms or redirects; Claude edits. The skill *is* the L4 substitute.
6. **One change at a time, by default.** Bundled edits (e.g. prompt + threshold together) cloud attribution: when the next run improves, you can't tell which change helped. The default is one change per iteration. Bundling is fine when the operator explicitly accepts the attribution loss — usually because both edits target the same observed failure, or the interaction is already well-understood. Treat the default as a tiebreaker, not a rule.
7. **General fix, not specific.** When L1 misbehaves on round 3 with input X, the prompt edit must guard against the *class* of mistake. Re-run to verify.

## Tracks

### 1. L1 behavior checks

`promptpotter/application/l1_behavior_checks.py` (planned — landing site after V10 hoist). Registry of programmatic checks: `(round_dict, ctx) -> CheckResult`. Each check looks at one round's output and verdicts ✓/✗ + evidence string. Pure functions, no I/O.

| check_id | rule |
|---|---|
| `context_object_honored` | Each variant references at least one of the three `context_object` items in `changes_description` or new prompt-field text. |
| `param_scope_discipline` | No variant touches `temperature`/`max_tokens`/`reasoning_effort` while ≥1 prompt field has been unchanged for the past 2 rounds, or before round `param_unlock_round` (default 3). |
| `not_only_param_variants` | ≥1 variant per round mutates a `PROMPT_STRING_FIELDS` field. |
| `optimizer_rewind_guard` *(5b — wired only when L2/L3 rebase emission lands)* | If the round emitted a `RebaseAction`, target round must exist + be ≤ current round; `reason` non-empty; payload non-empty (no-op rebase = bug). |

Adding a check = one-function diff. New unknown unknowns land here as the operator iterates.

`l1_generate.json` revised in the same commit to encode the two seeded constraints. First post-edit run is the framework's own first test.

### 2. Per-cycle `review.md`

`promptpotter/application/review.py` (planned — landing site after V10 hoist). Pure renderer (peer of `presentation/views/log_md.py`).

```python
def render_review_md(
    index: dict[str, Any],
    rounds: list[dict[str, Any]],
    *,
    prompt_snapshots: dict[str, dict[str, Any]] | None = None,
) -> str: ...
```

**Per-round section:**
- L1 inputs: `task_context` (broadcast L2-refined framing), critique fed in, three `context_object` items.
- Behavior-check checklist (✓/✗ + evidence string).
- Variants table: `variant | composite | accuracy | Δ_parent | Δ_baseline | beat_parent | changes`.
- This round's critique (output of `l1_critique`).
- **Next-generation peek** (sweep mode only, when round was halted post-generation/pre-scoring): the `OptSearchPoint` traces L1 produced for the next round. Rendered as a compact `cand_id | changes | derived_axes` table. No fitness columns — it wasn't measured.

**Header:** four prompt-template hashes, `L1Stats` block, behavior-violation summary, **round-1 verdict** (see Track 5).

Wired into `runner.py::finalize` AND emitted incrementally after round 1 so the round-1 gate has something to read. `tests/test_artifact_parity.py::PER_CYCLE_AUDIT_ARTIFACTS` gains `review.md`.

### 3. L1Stats

`compute_l1_stats(rounds, *, baseline_composite, behavior_results) -> L1Stats`. Frozen dataclass.

| metric | formula | role |
|---|---|---|
| `rounds_to_95` | first round where best accuracy ≥ 0.95; `None` if never reached | **headline** |
| `round_1_verdict` | `healthy` / `degraded` / `broken` per Track 5 rule table | **gate signal** |
| `yield_rate` | mean over rounds of (variants beating parent / variants generated) | diagnostic |
| `top_lift_mean` | mean over rounds of (best variant composite − parent composite) | diagnostic |
| `behavior_pass_rate` | (round × check) cells passing / total | diagnostic |
| `stagnation_max` | longest run with `top_lift ≤ 0` | diagnostic |
| `l2_fires` | rounds with `lineage.source == l2_context` | diagnostic |
| `proxy_lift_corr` (cross-cycle, computed by leaderboard) | Spearman rank correlation between round-1 `top_lift` and `rounds_to_95`-or-final-acc across qualifying cycles | **proxy validity** |

Parent composite: `baseline_composite` for round 1, `trials[r-1].composite` thereafter.

### 4. Cross-cycle / cross-branch leaderboard

`promptpotter/application/leaderboard.py` (planned — landing site after V10 hoist). Read-only shim `scripts/ppot_review.py`.

Row: `branch_path | dataset | pipeline | l1_generate_hash[:8] | l1_critique_hash[:8] | mode | fork_trigger | rounds_to_95 | round_1_verdict | round_1_top_lift | round_1_yield | best_acc | baseline_acc | Δacc | behavior_pass_rate | rounds_completed | l2_fires | stop_reason`.

`branch_path` = `root_cycle_id → ... → leaf_cycle_id` (renders the branch's lineage in one column). `fork_trigger` ∈ `{none, operator_sweep, operator_rewind, l2_rebase, l3_rebase, scoring_divergence}` (none = root cycle of a family).

`mode` = `sweep` (1 round + gen peek; emitted by sweep-triggered forks) or `full` (≥ 2 scored rounds).

Two views:
- **Default** (`--leaderboard`): all branches across all family-roots, sorted `(l1_generate_hash, rounds_to_95 asc with None last, behavior_pass_rate desc)`.
- **Sweep** (`--sweep`): branches with `fork_trigger = operator_sweep` only, grouped by parent `root_cycle_id`, sorted by `round_1_top_lift` desc within each group. Used for narrowing down candidate L1 prompts before promoting to full runs.

Computes `proxy_lift_corr` across all branches where the same `l1_generate_hash` exists in both modes — answers "does round-1 lift predict full-cycle outcome?". Reported in the table footer. Stdout-only. Not a new write verb.

### 5. Unified fork primitive (`--sweep` is the first caller)

Today `DecisionRecord(kind=FORK_CUT)` is scoring-divergence-only and its payload is `{from_round, forked_at}`. Generalize it to a typed action that **any trigger** can emit. Operator sweep is one caller; future L2/L3 rebase, L4 auto-rebase, M11 manual rewind, M12 pipeline-switch, and M11/M12 webapp-replay are additional callers built on the same code path. **One mechanism, multiple drivers.**

**Why this shape (vs the 0.1.0 `--sweep` halt-flag).** Sweep, L2-rebase-to-historical-parent, L3-meta-replan, and scoring-divergence all answer the same shape of question: *"abandon this lineage from round N, start over from round M < N with a different payload."* They differ only in (a) who issues the cut, (b) what payload differs at the cut. Building one primitive per driver compounds tech debt. Building the typed primitive once means new drivers (L4, webapp, multi-pipeline) ship as small callers rather than re-architecture.

**Domain extension** (`promptpotter/domain/run_records.py`):

```python
class ForkTrigger(StrEnum):
    OPERATOR_SWEEP    = "operator_sweep"
    OPERATOR_REWIND   = "operator_rewind"     # M11 — labelled manual fork from any round
    L2_REBASE         = "l2_rebase"            # M11 — gated behind --allow-llm-rebase
    L3_REBASE         = "l3_rebase"            # M11 — gated behind --allow-llm-rebase
    SCORING_DIVERGENCE = "scoring_divergence"  # already wired; today's only trigger
    # M12+ additions land as enum members; payload + mechanism unchanged:
    # PIPELINE_SWITCH, COMPETITOR_HARNESS, WEBAPP_REPLAY, L4_AUTO_REBASE

class ForkPayload(BaseModel):
    """The diff that distinguishes the fork from its parent at the cut point.
    Trigger-agnostic — every trigger uses the same payload shape."""
    trigger: ForkTrigger
    reason: str                          # mandatory; LLM-issued reason or operator label
    issued_by: str                       # operator id, "L2", "L3", "L4", or "system"
    # Optional deltas; any subset may be set:
    brief: str | None = None
    l1_section_overrides: dict[str, bool] | None = None
    l1_section_overrides_text: dict[str, str] | None = None
    l1_template_override: str | None = None
    pipeline_swap: dict | None = None    # M12 — connector / pipeline change at the cut
    scoring_swap: dict | None = None     # forward-compat: explicit scorer change at fork
```

`DecisionRecord(kind=FORK_CUT)` continues to carry `inputs_ref={"from_round": N}` and `outcome=new_cycle_id` (existing wire shape preserved); the new payload moves into `data.fork: ForkPayload`. Existing scoring-divergence forks keep working — runner backfills `trigger=SCORING_DIVERGENCE`, `issued_by="system"`, `reason="scorer_mismatch:<decision_kind>"`.

`FORK_CUT` stays `ARCHIVAL` in `DECISION_GATING` — its outcome (the new cycle_id) is downstream of the divergence-checked decisions, never gating itself.

**Mechanism.** Rename `_fork_at_divergence()` → `_mint_fork(parent, fork_from_round, payload: ForkPayload)` in `application/optimization/cycle.py`. Body unchanged: parent ledger gets `FORK_CUT` (now carrying `data.fork`), fork dir minted under `campaigns/{root}/forks/{cycle_id}/`, rounds/candidates copied for rounds `< fork_from_round`, active pointer retargeted, `CycleLedger.inherit_from(parent, parent.next_offset)`. The scoring-divergence branch in `cycle.py:790` becomes one caller; sweep + future LLM-rebase are new callers.

**LLM-side surface.** Extend `OptimizerAction` in `application/optimization/pipeline.py`:

```python
class OptimizerAction(BaseModel):
    # ... existing L1-surface fields ...
    rebase: RebaseAction | None = None  # ForkPayload-shaped sans `trigger`/`issued_by`
                                         # (set by runner from the layer that emitted it)
```

L2 and L3 emit `rebase` through their existing JSON-output channel — no new optimizer-side plumbing. **L1 cannot rebase** (asymmetry: only outer layers (L2/L3) may; L1 is the thing being rebased *on*).

**Operator caller (M10 deliverable).** `optimize --sweep` becomes a thin harness over the primitive:

1. Read N L1-candidate payloads from `datasets/{name}/sweep/*.json` (one `ForkPayload` per candidate, `trigger=OPERATOR_SWEEP`, `issued_by=<operator>`).
2. For each candidate: `_mint_fork(root_cycle, fork_from_round=1, payload=candidate_i)`, run round 1 (full: generate + score) → round 2 generation only (no scoring) → halt.
3. Round-2 round file written with `status: "generation_only"`, no `composite`/`accuracy`.
4. `index.json::final.mode = "sweep"` and `index.json::fork.trigger = "operator_sweep"` distinguish the branch.

All sweep branches share their parent's baseline measurements via the `archive/` cache — `(JobSearchPoint, sample) → hit` reuse is automatic across branches of the same family.

**LLM-rebase callers (M11 deliverable, scoped here for forward compat).** Out of M10 implementation, but the primitive must support:

- L2 rebase fires when accumulated `RuntimeFailure` trail contaminates the current branch beyond what L2's L1-surface writes can fix. Gated behind: (a) `--allow-llm-rebase` opt-in (default off until calibrated), (b) `l2_rebase_patience` counter (default `l2_patience × 2`), (c) hard cap of one L2 rebase per branch.
- L3 rebase fires when the whole L2 alley has stalled and L3's brief-rewrite is insufficient. Same gating shape with `l3_rebase_patience`, hard cap of one L3 rebase per branch.
- `OPTIMIZER_REWIND_GUARD` behavior check (Track 1): rebase target round must exist + be ≤ current round; reason field non-empty; payload non-empty (no-op rebase = bug).

**Telemetry.** Stays at family root. The active fork's `cycle_id` already lives at `dashboard.json::cycle_id`; extend with `cycle_id_path: list[str]` (root → ... → current) so the live view + webapp can render the branch tree without walking ledgers.

**Forward compat (≥M13).** New trigger types are enum additions. New payload deltas are optional fields. The mechanism (parent FORK_CUT + ledger inherit + dir copy + active-pointer retarget) does not change. Webapp-driven replay, multi-pipeline parallel evaluation, L4 auto-rebase, and competitor harnesses all ship as new callers + new enum members, never as schema or code-path changes.

### 6. Round-1 gate + analysis skill

The skill is the L4 substitute. Mandatory after round 1 in full mode; required as the comparison harness in sweep mode.

**`.claude/skills/potter-review/SKILL.md`** — new skill (or extension of `/potter-run`). Two modes:

**Single-cycle mode (default):** triggered after each full `optimize` run.
1. Read `campaigns/{cycle_id}/review.md` + `dashboard.json` + the round JSONs.
2. Compute `round_1_verdict`:
   - `healthy` — all behavior checks ✓, `yield_rate ≥ 0.20`, `top_lift > 0`. → operator may continue rounds 2-5.
   - `degraded` — exactly one check ✗, OR `yield_rate < 0.20`, OR `top_lift ≤ 0`. → halt, fix one knob, restart.
   - `broken` — ≥2 checks ✗, OR baseline regression. → halt, full prompt revisit.
3. Identify the top issue (rank: failed seeded check > failed scaffolding check > low yield > flat lift > L2-source-of-lineage in round 1).
4. Propose one specific edit to one specific prompt file, with diff.
5. Operator confirms / redirects. Claude applies the edit. Operator re-runs.

**Sweep mode (`/potter-review --sweep`):** triggered after a batch of `optimize --sweep` runs.
1. Collect all branches where `index.json::fork.trigger == "operator_sweep"` and parent `root_cycle_id` matches the active sweep batch.
2. Render side-by-side comparison under one root: round-1 metrics + round-2 next-generation peek per branch.
3. Rank by `round_1_top_lift` desc, tiebreak by `behavior_pass_rate`.
4. Highlight the top 2-3 to promote to full `optimize` runs; flag any branch with `round_1_verdict == broken` for prompt revisit.
5. Once paired (sweep, full) branches exist for ≥ 4 candidates, report `proxy_lift_corr` and recommend whether to keep using sweep mode per the proxy validation procedure (Track 7).

The skill never triggers `optimize` itself — the operator owns runs. The skill's only side effect on approval is editing prompt files.

**Round-1 gate is mandatory in full mode.** A full-mode run that proceeds past round 1 with `round_1_verdict ≠ healthy` is an operator override; the skill warns but does not block.

### 7. Methodology doc

`docs/methods/manual-prompt-tuning.md`:

- Goal restatement.
- Operating principles (above).
- **Sweep workflow:** run N candidate L1 prompts via `optimize --sweep` (each = baseline + round 1 + round-2 gen peek). Trigger `/potter-review --sweep` → side-by-side comparison on round-1 stats + next-gen peek → narrow to top 2-3 → promote those to full `optimize` runs → measure `proxy_lift_corr` after ≥ 4 paired (sweep, full) cycles.
- The five-step cadence (single-cycle): run round 1 → trigger `/potter-review` → confirm fix → apply → re-run.
- Round-1 verdict rule table.
- Diagnosis tree: behavior ✗ → fix the rule generally; all ✓ + low yield → broaden L1 creativity; all ✓ + flat lift → scoring/sample-set issue; early `l2_fires` → likely `l1_critique` weak.
- **Bundling carve-out:** the one-change default is a tiebreaker, not a mandate. Document the operator's bundling decision in the cycle note when it happens (e.g. "prompt + `param_unlock_round` together — both target the early-temperature-mutation failure").
- **Proxy validation procedure:** when `proxy_lift_corr ≥ 0.6` over ≥ 4 paired cycles, sweep-mode is trusted as the primary screening tool. When `< 0.4`, sweep-mode is suspended; revisit the rule table and either tighten round-1 metrics or accept that 2-round screening is the minimum. Between 0.4 and 0.6, run sweep + 1 confirmation full-cycle per candidate.
- **Generalization gate:** re-run on the second pipeline before promoting a prompt to default. Promote-only, not every iteration.
- Adding a new behavior check.
- Stopping criteria.

## Wave sequencing

Reordered for **biggest-blocker first**: Track 5a unblocks the breadth-first sweep workflow on day one. Without it, the operator workflow stalls and downstream tracks have no sweep data to consume. Subsequent tracks layer on as data accumulates.

1. **Track 5a — unified fork primitive (M10).** Domain extension (`ForkTrigger`, `ForkPayload`) + `_mint_fork()` generalization + scoring-divergence-path retrofit + `optimize --sweep` as first operator caller. `OptimizerAction.rebase` schema field added but unwired (no L2/L3 emission yet). **This is the gating deliverable** — every other track produces or consumes sweep-cycle data.
2. **Tracks 1 + 3 (parallel).** L1 behavior checks + `L1Stats`. Pure-Python over loaded dicts; run against sweep round-1 data as it accumulates. Manual reading of round JSONs covers the analysis gap until these land.
3. **Track 4 — branch-tree leaderboard.** Consumes 5a fork metadata + Track 3 stats. First cross-branch comparison view; sweep candidates ranked side-by-side.
4. **Track 2 — `review.md` renderer.** Consumes 1 + 3 output; per-fork view. `runner.py::finalize` wiring + parity test + incremental round-1 emission + next-generation peek.
5. **Track 6 — `/potter-review` skill** (single-cycle + sweep modes). Consumes 2 + 4.
6. **Track 7 — methodology doc** + `l1_generate.json` revision.
7. **Track 5b — LLM-rebase callers (M11).** L2/L3 rebase emission + patience gating + `--allow-llm-rebase` opt-in + `optimizer_rewind_guard` behavior check. `optimize --rewind --label X` second operator caller. Out of M10; lands once 5a + sweep workflow have produced calibration data.
8. **Iterate.** Each new "unknown unknown" → new behavior check + skill rule update; each batch of sweep branches updates `proxy_lift_corr`.

## Entry / exit

**Entry:** M9 exit gate.

**Exit:**
- All seven deliverables shipped, tests green.
- `rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`.
- `behavior_pass_rate = 1.0` for both seeded checks across the qualifying cycles.
- `/potter-review` skill demonstrably catches at least one prompt regression on a re-run before round 2 fires.
- **Proxy hypothesis decision recorded** — either `proxy_lift_corr ≥ 0.6` over ≥ 4 paired branches (sweep validated as primary screening tool), or sweep-mode rules modified per the proxy validation procedure with the rationale documented in the methodology doc.
- Methodology doc maps section-by-section to actual `review.md` output.
- **Unified `_mint_fork()` covers both scoring-divergence and operator-sweep callers under one code path.** `Decision(kind=FORK_CUT).data.fork: ForkPayload` populated for both. `OptimizerAction.rebase` schema field present (unwired in M10; M11 wires L2/L3 emission).

## Out of scope

- Auto-tuning prompts (L4) → [`m12-plus-backlog.md`](m12-plus-backlog.md).
- Test-set validation → M11.
- Third pipeline → M12 multi-connector.
- Webapp surfacing → M11/M12.
- Variant-vs-sample heatmap in `review.md` — deferred unless seeded checks prove insufficient.

## Open decisions

- `param_unlock_round` default: 3.
- `round_1_verdict` thresholds: `yield_rate ≥ 0.20` for healthy, `< 0.20` for degraded. Calibrate after first 3 cycles.
- Evidence strictness: substring match. Upgrade to semantic if false-pass rate misleads iteration.
- Leaderboard persistence: stdout only.
- New skill vs extension of `/potter-run`: new skill (cleaner separation). Confirm.
- **Sweep-payload source**: `datasets/{name}/sweep/*.json` (one ForkPayload per file) vs stdin vs CLI-flag-array. Default to filesystem layout for git-tracked reproducibility; revisit if operator friction shows up.
- **L2/L3 rebase patience defaults** (5b): proposed `l2_rebase_patience = l2_patience × 2`, `l3_rebase_patience = l3_patience × 2`. Calibrate during M11 once first L2/L3 rebase events ship.
- **`cycle_id_path` exposure**: add to `dashboard.json` only, or also to `index.json::final` for offline branch-tree reconstruction. Default to both — cheap, makes webapp wiring trivial later.

## Key existing code

| Area | Files |
|------|-------|
| Optimizer prompts | `promptpotter/application/optimization/optimizer_pipeline.json::resolved_prompts['{l1_generate,l1_critique,l2_context,l3_plan,restructure}/1']` |
| Optimizer output schemas | `optimizer_pipeline.json::resolved_schemas['{l1_generate,l1_critique}/1']` (`l1_generate` is the static envelope; `build_l1_output_schema` grafts per-target node properties on top) |
| Prompt loading | `pipeline.py::load_optimizer_prompt` (manifest registry → Langfuse override) |
| Per-round trace | `campaigns/{cycle_id}/.cache/rounds/round_NNNN.json` (`nodes.l1_generate`, `nodes.l1_score`, `nodes.l1_critique`) |
| Per-round optimizer state | `campaigns/{cycle_id}/rounds/round_NNNN.json` |
| Prompt snapshots | `campaigns/{cycle_id}/prompts/optimizer_prompt/{hash}/` |
| Existing log renderer | `presentation/views/log_md.py::render_log_md` (purity pattern) |
| Reusable formatters | `presentation/views/display.py` |
| Surface compile path | `application/optimization/pipeline.py::compile_l1_surface`, `compile_l2_surface`, `compile_l1_critique_blob` |
| Cycle finalize / round emission | `application/runner.py` |
| Existing operator skill | `.claude/skills/potter-run/SKILL.md` (extend or peer) |
| Parity test | `tests/test_artifact_parity.py::PER_CYCLE_AUDIT_ARTIFACTS` |
| **Fork primitive (Track 5 substrate)** | `domain/run_records.py::DecisionKind.FORK_CUT` + `DECISION_GATING`; `infrastructure/ledger.py::CycleLedger.inherit_from`; `application/optimization/cycle.py::_fork_at_divergence` (rename → `_mint_fork`); `application/runner.py::fork_on_divergence` plumbing; `presentation/cli/campaign_runner.py::--fork-on-divergence` CLI; `presentation/api.py` ForksResponse |
| **Active-pointer + family-root binding** | `infrastructure/store/stores.py::save_active_pointer`; `infrastructure/projections/live_dashboard.py` (telemetry binds to root with no `parent_cycle_id`) |

## Risks

| Risk | Mitigation |
|------|------------|
| Proxy hypothesis fails (round-1 doesn't predict full-cycle) | Detected by `proxy_lift_corr`; framework modifies on the go (revisit rule table or accept 2-round screening) — that's what Track 7's proxy validation procedure is for |
| Sweep mode hides regressions that only surface in round 2-3 | Pair every promoted candidate with a full-cycle confirmation run; never publish a prompt that only has sweep-mode evidence |
| Round-1 gate triggers on noise, halts a run that would have recovered | Operator can override; skill warns but doesn't block. Calibrate thresholds after first 3 cycles. |
| Substring match too coarse (L1 superficially echoes a token) | Upgrade to semantic only if false-pass rate misleads iteration |
| Behavior-check registry grows ad-hoc, rules contradict | Methodology doc owns the running list; consolidate periodically |
| `l1_generate.json` over-restricted, yield drops | One knob per iteration + general-fix rule keep edits reversible; leaderboard catches regression |
| 95% unreachable on TermNorm under current pipeline | Track per-pipeline `rounds_to_95` separately; recalibrate at exit-gate review |
| Skill proposes wrong fix, operator follows blindly | Methodology doc forbids "apply without thinking"; operator confirms/redirects every edit |
