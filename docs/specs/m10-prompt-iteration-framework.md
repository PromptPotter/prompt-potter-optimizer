# M10: Prompt-Iteration Framework + L1-generate Tuning

**Version:** 0.1.0
**Date:** 2026-04-28
**Status:** Planned
**Depends on:** M9

---

## Goal

≥95% training-set accuracy in ≤5 rounds on `llm_only` AND TermNorm under the same prompt revision. ~1 week of manual iteration once framework lands. Then M11 picks up for test-set + benchmarks.

## L4 partial — what this also is

The framework doubles as the partial implementation of L4 self-optimization (see [`m12-plus-backlog.md § Self-optimization`](m12-plus-backlog.md)). L4 has two blockers: (1) credit assignment / cheap proxy reward; (2) "PromptPotter-as-backend" adapter. **M10 closes most of (1)**: `proxy_lift_corr` is the credit-assignment validation, `optimize --sweep` is the cheap-trial mechanism, behavior checks are the programmatic conformance signal, `review.md` + `L1Stats` are the per-cycle structured features. (2) stays in M12+. Architectural consequence: **target ≤ ~400 LOC of new code**, all reusing existing primitives (`AuditTrailProjection`, `OptSearchPoint` traces, `MeasurementArchive`, `formatting.py`, scipy.stats). No new persistence, no new abstraction layer. If a proposed component doesn't forerun L4, push back.

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
| `l2_directive_followed` | If `opt_search_point.l2_directive` non-empty, ≥1 variant's `changes_description` references a key noun phrase from it. |
| `not_only_param_variants` | ≥1 variant per round mutates a `PROMPT_STRING_FIELDS` field. |

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
- L1 inputs: `l2_directive`, critique fed in, three `context_object` items.
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

### 4. Cross-cycle leaderboard

`promptpotter/application/leaderboard.py` (planned — landing site after V10 hoist). Read-only shim `scripts/ppot_review.py`.

Row: `cycle_id | dataset | pipeline | l1_generate_hash[:8] | l1_critique_hash[:8] | mode | rounds_to_95 | round_1_verdict | round_1_top_lift | round_1_yield | best_acc | baseline_acc | Δacc | behavior_pass_rate | rounds_completed | l2_fires | stop_reason`.

`mode` = `sweep` (1 round + gen peek) or `full` (≥ 2 scored rounds).

Two views:
- **Default** (`--leaderboard`): all cycles, sorted `(l1_generate_hash, rounds_to_95 asc with None last, behavior_pass_rate desc)`.
- **Sweep** (`--sweep`): sweep-mode cycles only, sorted by `round_1_top_lift` desc. Used for narrowing down candidate L1 prompts before promoting to full runs.

Computes `proxy_lift_corr` across all cycles where the same `l1_generate_hash` exists in both modes — answers "does round-1 lift predict full-cycle outcome?". Reported in the table footer. Stdout-only. Not a new write verb.

### 5. Sweep mode — `optimize --sweep`

CLI flag on `optimize`: `--sweep` runs **baseline → round 1 (full: generate + score) → round 2 generation only (no scoring) → halt**. Round-2's L1 output lands in `nodes.l1_generate.output.response.variants[]` of `round_0002.json` exactly as in a full run; the round-2 trial JSON is written with `status: "generation_only"` and no `composite`/`accuracy` fields. `index.json::final.mode = "sweep"` distinguishes the cycle from a full run.

Reuses existing persistence (round_recorder, OptSearchPoint trace archival, prompt snapshots) — no new infra. The framework just halts before `score_population` for round 2.

Sweep cycles feed the leaderboard's `--sweep` view and the `proxy_lift_corr` computation. They are first-class cycles for cross-cycle comparison; their `cycle_id`s appear next to full-mode cycles using the same `l1_generate_hash`.

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
1. Collect all cycles where `index.json::final.mode == "sweep"` and `cycle_id` belongs to the active sweep batch.
2. Render side-by-side comparison: round-1 metrics + round-2 next-generation peek per cycle.
3. Rank by `round_1_top_lift` desc, tiebreak by `behavior_pass_rate`.
4. Highlight the top 2-3 to promote to full `optimize` runs; flag any cycle with `round_1_verdict == broken` for prompt revisit.
5. Once paired (sweep, full) cycles exist for ≥ 4 candidates, report `proxy_lift_corr` and recommend whether to keep using sweep mode per the proxy validation procedure (Track 7).

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

1. Tracks 1 + 3 (parallel — pure-Python over loaded dicts).
2. Track 2 (renderer + wiring + parity test + incremental round-1 emission + next-generation peek).
3. Track 5 (`optimize --sweep` flag — small surgery in `runner.py` to halt before round-2 scoring).
4. Track 4 (leaderboard with sweep view + `proxy_lift_corr`).
5. Track 6 (`potter-review` skill, both modes).
6. Track 7 (methodology) + `l1_generate.json` revision.
7. **Iterate.** Each new "unknown unknown" → new behavior check + skill rule update; each batch of sweep cycles updates `proxy_lift_corr`.

## Entry / exit

**Entry:** M9 exit gate.

**Exit:**
- All seven deliverables shipped, tests green.
- `rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`.
- `behavior_pass_rate = 1.0` for both seeded checks across the qualifying cycles.
- `/potter-review` skill demonstrably catches at least one prompt regression on a re-run before round 2 fires.
- **Proxy hypothesis decision recorded** — either `proxy_lift_corr ≥ 0.6` over ≥ 4 paired cycles (sweep validated as primary screening tool), or sweep-mode rules modified per the proxy validation procedure with the rationale documented in the methodology doc.
- Methodology doc maps section-by-section to actual `review.md` output.

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

## Key existing code

| Area | Files |
|------|-------|
| Optimizer prompts | `promptpotter/application/optimization/prompts/{l1_generate,l1_critique,l2_context,l3_plan}.json` |
| Prompt loading | `pipeline.py::load_optimizer_prompt` (line 244) |
| Per-round trace | `campaigns/{cycle_id}/.cache/rounds/round_NNNN.json` (`nodes.l1_generate`, `nodes.l1_score`, `nodes.l1_critique`) |
| Per-round optimizer state | `campaigns/{cycle_id}/trials/trial_NNNN.json` |
| Prompt snapshots | `campaigns/{cycle_id}/prompts/optimizer_prompt/{hash}/` |
| Existing log renderer | `presentation/views/log_md.py::render_log_md` (purity pattern) |
| Reusable formatters | `presentation/views/display.py` |
| Surface compile path | `application/optimization/pipeline.py::compile_l1_surface`, `compile_l2_surface`, `compile_l1_critique_blob` |
| Cycle finalize / round emission | `application/runner.py` |
| Existing operator skill | `.claude/skills/potter-run/SKILL.md` (extend or peer) |
| Parity test | `tests/test_artifact_parity.py::PER_CYCLE_AUDIT_ARTIFACTS` |

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
