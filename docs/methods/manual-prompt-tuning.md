# Manual Prompt Tuning — M10's Iteration Framework

> **Status:** active. Methodology version pinned to `application/optimization/l1_stats.py::HEALTHY_YIELD_RATE` and `HEADLINE_ACC` — when those constants change, this doc changes too.

The framework gives the operator + Claude the same quality of feedback an L4 self-optimizer would give itself, but manually and fast. Use it to tune `l1_generate` (the loop's principal bottleneck) toward the M10 exit gate: **≥95% in ≤5 rounds on `llm_only` AND TermNorm under the same `l1_generate_hash`**.

---

## Goal

Each round costs LLM credits and operator attention. The framework reduces wasted rounds two ways:

1. **Round-1 gate.** Most diagnostic signal is in round 1. Halt + review before committing rounds 2–5.
2. **Sweep mode.** When comparing N candidate L1 prompts, run each as 1 scored round + 1 generation peek (no round-2 scoring). Promote winners to a full run instead of running 5 full rounds per candidate.

Goal validity is itself a hypothesis: sweep results predict full-cycle outcomes. Tracked via `proxy_lift_corr` and modified on the go (not before).

## Operating principles

1. **Each searchpoint is costly.** Treat rounds as scarce. Round-1 gate is mandatory.
2. **Round-1 sweep, round-2 promote.** N candidates → `optimize --sweep` per candidate → side-by-side comparison → promote top 2-3 to full runs.
3. **Proxy validation is a first-class question.** `proxy_lift_corr` answers it. If `≥ 0.6` over ≥ 4 paired cycles, sweep is the primary screen. If `< 0.4`, suspend it.
4. **Build the framework before and while running.** New unknown unknowns become new behaviour checks; the registry grows with the iteration.
5. **Skill-collaborative analysis, not solo runs.** `/potter-review` reads the artifacts, surfaces the top issue, proposes one fix. Operator confirms/redirects.
6. **One change at a time, by default.** Bundling carve-out: when both edits target the same observed failure (or the interaction is well-understood), the operator may bundle. Document the bundling decision in `notes.md`.
7. **General fix, not specific.** When L1 misbehaves on input X, the prompt edit guards against the *class* of mistake. Re-run to verify.

---

## Sweep workflow (multi-candidate)

```
1. Edit l1_generate.json (or another optimizer prompt) — candidate A.
2. python -m promptpotter optimize --sweep
   → cycle_A: baseline + 1 full round + 1 gen-only round.
3. Repeat for candidates B, C, D, ... (each is its own cycle).
4. /potter-review --sweep
   → ranked by round_1_top_lift, with the next-gen peek for each.
5. Promote top 2-3 candidates to full optimize runs.
6. After ≥ 4 (sweep, full) pairs share an l1_generate_hash:
   → proxy_lift_corr in the leaderboard footer drives the verdict.
```

Promotion = the operator runs `python -m promptpotter optimize` (no `--sweep`) on the candidate's prompt, gets a full 5-round run, and the leaderboard now has both modes for that hash.

## Crossover iteration loop (dataset-portable)

When sweep candidates stall at a plateau and single-axis perturbations stop yielding lift, switch to crossover. Composes proven genes from the family's best arms with new genes derived from the archive's miss-pattern matrix. **Same loop runs verbatim for any dataset** (BBEH, AIME, GSM8K, …); only the dataset name and the crossover payloads change.

### Pre-reqs

- At least one cycle with `final.winner_pipeline_params` on disk for the dataset.
- An `archive/measurements/` populated with predicted-vs-ground-truth records (i.e. one full `optimize` run, not just `--sweep`).

### Phases

```
A. Bootstrap signal      — full optimize (no flag) to seed measurements.
B. Diag-spawn (3-5x)     — optimize --diag, repeated, to generate candidate L1-surface mutations under L2 pressure.
C. Cross-cycle ranking   — compare --all --max-topups -1, abort on Ctrl+C when leader is clear.
D. Failure analysis      — parse archive/measurements/*.json; build (parsed_answer, ground_truth) confusion matrix; identify dominant miss patterns covering ≥50% of miss mass.
E. Crossover composition — write 4 sweep payloads to datasets/{name}/sweep/; each payload mixes ONE proven gene (winner attribute from the leader cycle) with ONE weakness-targeting gene (instruction text addressing a top-3 miss pattern).
F. Re-sweep              — set active pointer to the leader cycle, archive prior payloads, optimize --sweep.
G. Re-rank               — compare --all again to see which crossovers beat the leader.
H. Promote               — full optimize on the best crossover's cycle. Round-trips back to phase D with new evidence.
```

### Knobs per phase

- **Phase B:** 3–5 diag forks gives enough variance for L2 to write distinct directives. Stop adding when the L2-evolved surfaces start repeating themselves.
- **Phase D:** the confusion matrix is `(predicted, ground_truth)` keyed; aggregate counts across all `archive/measurements/*.json`. The dominant pattern usually accounts for 30–50% of miss mass. Treat patterns above 5% as worth a dedicated crossover gene.
- **Phase E:** 4 payloads is the right cardinality for a single iteration — enough to cover the top-3 miss patterns + 1 controlled compositional test of the family's two top-prior-lift attributes; few enough to read each crossover's effect by hand if R2-scoring isn't yet wired.
- **Phase F:** **payload dedup is per-parent.** If you re-run `--sweep` from the same parent, prior batches' payloads are skipped. Switching parents (e.g. moving the active pointer from family root to the leader cycle) resets dedup, so all payloads in the dir fork fresh. Use `datasets/{name}/sweep/.archive/` to hide payloads from the loader (the glob is non-recursive).

### Crossover payload schema

Each payload is JSON conforming to `domain.run_records.SweepPayload`. The fields that matter:

| field | purpose |
|---|---|
| `reason` | one-paragraph hypothesis. Names the proven gene + the weakness-targeting gene + the conjecture under test. |
| `directive` | becomes `opt_sp.l2_directive`; primary channel for steering L1-generate. State both genes explicitly and require their composition (not single-axis). |
| `l1_section_overrides` | dict of section_name → bool; visibility toggles (e.g. `{"axes_l1": false}` to hide an L1 catalogue section). |
| `l1_section_overrides_text` | dict of section_name → text; replace the section content with dataset-specific framing or miss-pattern intelligence. `task_context` is the most common target — carries archive-derived facts L1 can use. |

A worked example sits at `datasets/bbeh/sweep/09_format_x_no_unknown.json` through `12_format_x_polarity_check.json`: each composes D002's tight 3-step `answer_format` (proven +21pp, n=20) with a different weakness-targeting gene (anti-"unknown"-cop-out, expert persona, rule-citation, polarity-check). Read those four files as the canonical pattern when porting to a new dataset.

### Known blocker (status: open)

Sweep mode's round 2 is generation-only. Crossovers produce different R2 candidates per payload but those candidates are not scored, so `compare --all` rank-ties all sweep-fork siblings on their (cache-replayed) R1 winner and the crossover effect is invisible to PoBB. Two workarounds until R2 scoring lands:

1. **Read R2 candidates by eye.** Each fork's `trials/trial_0001.json::opt_search_point` carries the L1-generated R2 candidates. Best one wins by inspection.
2. **Promote the most promising fork by hand.** Move active pointer to that fork, run plain `optimize` (no `--sweep`), let R2 score normally.

The clean fix is wiring `execute_round` into `_run_round_loop` for sweep mode (5-line change). Until that lands, treat sweep as a candidate-generator and `optimize` as the candidate-scorer; do not treat sweep results as ranked.

### Generalization checklist (dataset port)

Porting this loop to a new dataset (AIME, GSM8K, HotPotQA, …):

1. Create `datasets/{name}/` with `pipeline.json`, `campaign.json`, `dataset.md`, `task_description.md` — same scaffolding as `datasets/bbeh/`.
2. Run phase A (bootstrap) once. Inspect baseline accuracy.
3. Run phase B (3–5 diag forks). The L2-evolved surfaces are dataset-specific — different miss profiles produce different directives.
4. Run phase D (failure analysis) against `library/.archive/measurements/{name}_*.json`. The miss-pattern matrix is dataset-specific (BBEH has "unknown" cop-outs; AIME will have arithmetic slips; GSM8K has unit-of-measure errors). The crossover genes change accordingly.
5. Phases E–H follow the same structure regardless of dataset. The 4 payload templates we wrote for BBEH (format-anchor + weakness-targeting) port mechanically — only the weakness-targeting gene's text changes.

The methodology is dataset-independent; the genes are dataset-specific.

---

## Five-step single-cycle cadence

```
1. python -m promptpotter optimize         # full mode
2. /potter-review                          # round-1 gate
3. Operator confirms or redirects the proposed fix.
4. Claude applies the edit (Edit tool, prompt file).
5. Operator re-runs optimize.
```

The skill is mandatory after round 1. A full-mode run that proceeds past round 1 with `round_1_verdict ≠ healthy` is an operator override; the skill warns but doesn't block.

---

## Round-1 verdict rule table

Source of truth: `application/optimization/l1_stats.py::compute_round_1_verdict`. Constants: `HEALTHY_YIELD_RATE = 0.20`, `HEADLINE_ACC = 0.95`.

| condition | verdict | next step |
|---|---|---|
| 0 ✗ AND `yield_rate ≥ 0.20` AND `top_lift > 0` | **healthy** | continue rounds 2–5 |
| ≥ 2 ✗ OR baseline regression at round 1 | **broken** | halt; full prompt revisit |
| anything else | **degraded** | halt; one-knob fix; restart |

Calibrate after the first 3 cycles if the threshold misclassifies. Update the constants AND the `/potter-review` SKILL table together.

## Diagnosis tree

| signal | likely cause | fix file |
|---|---|---|
| `context_object_honored` ✗ | task_context block too low in prompt | `l1_generate.json` |
| `param_scope_discipline` ✗ | param boundary too loose, or `param_unlock_round` too low | `l1_generate.json` (or `param_unlock_round` knob) |
| `l2_directive_followed` ✗ | L2 directive not elevated in L1's prompt | `l1_generate.json` |
| `not_only_param_variants` ✗ | L1 only mutates node params | `l1_generate.json` |
| all ✓ + `yield_rate < 0.20` | L1 too conservative | bump `creativity` or rewrite `l1_critique.json` |
| all ✓ + `top_lift ≤ 0` | scoring or sample-set issue, not a prompt issue | check `campaign.json::scoring`; check `dashboard.json::scoring_set` |
| early `lineage.source == l2_context` | `l1_critique` weak; L2 forced to fire | `l1_critique.json` |

Bundling carve-out: edits that target the same observed failure may bundle. Example: "prompt + `param_unlock_round` together — both target the early-temperature-mutation failure." Document in `notes.md` so attribution is recoverable later.

---

## Proxy validation procedure

Sweep-mode is a proxy for full-cycle outcome. Validate or refute it as data accumulates.

| `proxy_lift_corr` over ≥ 4 paired hashes | action |
|---|---|
| `≥ 0.6` | sweep is the **primary** screening tool. Use it freely. |
| `0.4 – 0.6` | sweep + 1 confirmation full-cycle per promoted candidate. |
| `< 0.4` | **suspend** sweep mode. Revisit the rule table; tighten round-1 metrics or accept 2-round screening as the minimum. |

The leaderboard footer reports `corr` + `n_pairs` + `verdict` automatically. When `n_pairs < 4`, the footer says `insufficient data` and falls back to "every candidate gets a full run".

If the framework gets modified (rule table, thresholds, scope), document the modification in this doc with the date and the cycle range that triggered it. The methodology is allowed to evolve mid-iteration; what's not allowed is silently drifting.

## Generalization gate

Before promoting a prompt to default, **re-run on the second pipeline**. M10's exit gate is `rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`. A candidate that hits ≤ 5 on one but not the other is not promoted; either tune further until both pass, or split the prompt into pipeline-specific variants and pay the maintenance cost knowingly.

Promote-only, not every iteration — the second pipeline is a confirmation run, not a sweep candidate.

---

## Adding a new behaviour check

When a new "unknown unknown" surfaces in a run:

1. Write a new check function in `application/l1_behavior_checks.py` matching the `(round_dict, ctx) -> CheckResult` signature. Keep it pure.
2. Add it to `CHECK_REGISTRY` (one line).
3. Update the `/potter-review` SKILL diagnosis tree with the new check_id and its fix recipe.
4. (Optional) Update this doc's diagnosis-tree table.

The first post-edit `optimize` run is the new check's own first test. If it never fires, either the rule is too lax or the failure mode never recurs — both are signal worth recording in `notes.md`.

## Stopping criteria

Stop iterating on the framework when **all** hold:

- `rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`.
- `behavior_pass_rate = 1.0` for both seeded checks across the qualifying cycles.
- `/potter-review` has demonstrably caught at least one prompt regression on a re-run before round 2 fired (operator-witnessed).
- `proxy_lift_corr` decision recorded — either trusted (`≥ 0.6` over ≥ 4 pairs) or sweep-mode rules modified per the proxy validation procedure with rationale documented above.

When all four hold, M10 exits. M11 picks up for test-set + benchmarks.

---

## References

- `docs/specs/m10-prompt-iteration-framework.md` — full spec
- `.claude/skills/potter-review/SKILL.md` — operator skill, round-1 gate enforcement
- `promptpotter/application/l1_behavior_checks.py` — check registry (4 seeded)
- `promptpotter/application/optimization/l1_stats.py` — `compute_round_1_verdict` + thresholds
- `promptpotter/application/review.py` — `review.md` per-cycle renderer
- `promptpotter/application/leaderboard.py` + `scripts/ppot_review.py` — cross-cycle leaderboard
- `docs/concepts/three-layer-loop.md` — L1/L2/L3 escalation model the framework tunes
