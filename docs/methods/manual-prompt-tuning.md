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
