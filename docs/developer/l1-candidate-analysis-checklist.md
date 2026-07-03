# L1 candidate analysis checklist

Load this when reviewing an L1 round trace — operator pastes a CLI dump,
asks "what does this tell you?", or you're acting on a round's behavior
checks (e.g. inside `/potter-l1-meta-campaign`). Walk every item below
before reporting findings. Skipping items has historically let
evidence-free or rule-violating L1 proposals through unflagged.

Each item names what to check, where the data lives, and what a
violation looks like. None of these are blanket-rejected by code today
(some are; see "Enforced by validators" at the end). For the unenforced
ones, your analysis IS the gate.

## A. Evidence availability in the rendered L1 input

For round 1 of any cycle (especially fresh forks): does the L1 prompt
input actually carry the signals it claims to consult?

- `axis_memory` — present iff `AxisIndex.ensure_for` had ≥1 prior
  archive measurement for the backend. Empty on a backend's first
  cycle. Inspect:
  `{cycle_dir}/.runtime/cache/rounds/round_0001.json::nodes.l1_generate.input.template_fields.problem_description`
  for the rendered `AXIS MEMORY (cross-cycle observations from
  MeasurementArchive):` block.
- `runtime_failures` — present iff either (a) intra-cycle L1 round
  produced one OR (b) `Cycle.start` inherited from sibling forks via
  `gather_sibling_runtime_failures`. Inspect the same rendered input
  block for `RUNTIME FAILURES (candidates ran but degraded):`. If
  empty in round 1 AND siblings exist that DID produce failures, the
  inheritance path is broken — investigate `sibling_wounds.py` and
  `_rf_matches_current_config` (the latter may be dropping
  cross-model failures intentionally).
- `critique` / `escalation_panel` — round-N>1 signals; empty in
  round 1 by design.

## B. Re-proposal of known-failing configs

For each candidate's `pipeline_params_override`: does any (param, value)
pair appear in `opt_sp.wounds.runtime_failures[*].observed_config`?

- Enforced by validator `L1_CONFIG_NOT_IN_RUNTIME_FAILURES`
  (`application/optimization/validators/l1_strict.py`) — a match
  produces `ValidationFailure(reason="reproposes_known_failing_config")`
  and the candidate is healed via Wound 1.
- The validator only catches EXACT (param, value) matches. If the L1
  proposes a different value (e.g. `max_tokens=2400` vs failed `1800`),
  no rejection. That's intentional — different values are legitimate
  exploration. Flag in analysis when a candidate proposes a value
  near a known-failing one without justification.

## C. PEAKED-axis mutations without justification

The HARD BLOCKS section of `l1_generate/1` says: if `axis_memory` marks
an axis as PEAKED, do NOT mutate it unless the critique names that axis
(`priority_fix` or `suggested_axes`), OR
`escalation_panel.exploration_budget == wide`.

This is **not** enforced by a validator today. Manual check:

- Read the rendered `AXIS MEMORY` block from the L1 input. Note axes
  marked `PEAKED — do not mutate unless the critique names this axis ...`.
- For each candidate whose `target_axis` is a PEAKED axis: read its
  `evidence_grounding.citation` — does it quote the critique naming
  that axis, or `escalation_panel.exploration_budget =
  wide`? If neither, flag as **`peaked_axis_violation`** in the
  analysis report and recommend adding a code-level validator
  (follow-up PR).

## D. Continuous-axis envelope (±50% of parent value)

HARD BLOCKS rule 3: for numeric knobs (`max_tokens`, `temperature`,
`top_p`), stay within ±50% of the parent value unless justified.

Not enforced by validator. Manual check:

- For each numeric `pipeline_params_override` value: compare against the
  parent's value (visible in the L1 input's `CURRENT PROMPT` block or
  the round-display table's `Parent` column).
- If the proposed value is outside ±50% of parent and the candidate's
  evidence_grounding doesn't cite the critique / runtime_failures /
  exploration_budget=wide, flag as **`envelope_violation`**.

## E. PARAM-FIELD axes are LAST RESORT

HARD BLOCKS rule 4: AT MOST 1 of `{{n_variants}}` candidates may target
a param-field axis (`temperature`, `max_tokens`, `reasoning_effort`,
`top_p`), AND only when `L1_CRITIQUE.suggested_axes` explicitly names
a param axis OR `runtime_failures` carries a quantitative signature.

Not enforced by validator. Manual check:

- Count candidates whose `target_axis` is in
  `{llm_only.temperature, llm_only.max_tokens, llm_only.reasoning_effort, llm_only.top_p}`
  (or backend equivalents). If > 1, flag as **`param_axis_overuse`**.
- For the one allowed, check: was either condition met (critique named
  it, or runtime_failures had a quantitative pointer)? If not, flag
  as **`unjustified_param_mutation`**.

## F. Intra-round paraphrase / mode collapse

Mode-collapse is when N candidates push the same conceptual mutation
across different axes (e.g. C1.1 puts "verify" in instruction, C1.2
puts "cross-check" in persona, C1.3 puts "validate" in thinking_style
— all the same idea, different slots).

Not enforced by validator today (the L2 stale-repeat detector at
`L2_TASK_CONTEXT_STALE_REPEAT` checks L2's output, not L1's; the
operator could request an L1-side equivalent as a follow-up).

Manual check using the same token-set Jaccard heuristic the L2 detector
uses:

- For each pair of candidates this round: compute Jaccard of word-sets
  (lowercase, regex `\w+`, length > 2) on `changes_description`.
- If any pair has Jaccard ≥ 0.5, flag as **`intra_round_paraphrase`**.
- Even when not violating the threshold, watch for shared THEME words
  across all candidates (verify, check, restate, validate). If
  ≥ N/2 candidates carry one of these, flag as **`theme_mode_collapse`**.

## G. Evidence-grounding actually grounds

For each candidate, check that `evidence_grounding.citation` is a real
quote from the named `field`, not a hallucination.

- `field=axis_memory` + citation `"llm_only.max_tokens (effect=0.242, ...)"`
  ⇒ verify the axis_memory block in the L1 input contains this row.
- `field=critique` ⇒ verify the quoted highlight / priority_fix
  appears in the rendered critique block.
- `field=stall_exploration` ⇒ valid only when
  `escalation_panel.exploration_budget ∈ {normal, wide}` — verify.

Not enforced as a strict citation-match validator (per the operator's
direction: the LLM is free to interpret all input as evidence; don't
require structured citations). But if the citation NAMES a field that
doesn't appear in the rendered input at all, that's a hallucination
and worth flagging.

## H. Output format integrity

- LaTeX escapes preserved (`\boxed{N}` not `oxed{N}`).
- No template placeholders (`{x}`, `[insert]`, `<query>`) in prompt-field
  values — the field semantics rule rejects these as templates.
- `pipeline_params_override` keys are real node names from the schema's
  `param_keys`. Invalid keys are caught by `L1_SCHEMA_COMPLIANCE`.

## Enforced by validators today (just for orientation)

| Check | Validator |
|---|---|
| Schema compliance (allowed-models, param_allowed_values, type) | `L1_SCHEMA_COMPLIANCE` |
| Forbidden axes (`model`, `provider`) | `validate_overrides(forbidden_axes_strict=True)` |
| Re-propose known-failing config | `L1_CONFIG_NOT_IN_RUNTIME_FAILURES` (this PR) |
| L2 task_context no-op merge / paraphrase repeat | `L2_TASK_CONTEXT_STALE_REPEAT` (evidence `mode`: `verbatim` \| `paraphrase`) |
| L2 duplicate insert (≥3 lines) | `L2_DUPLICATE_INSERT` |
| L3 plan length floor / verbatim repeat | `L3_PLAN_LENGTH_FLOOR`, `L3_PLAN_VERBATIM_REPEAT` |

Everything else above is your analysis responsibility.

## Reporting format

When you find one or more violations, list them as a checklist at the
top of your analysis reply, before any narrative interpretation:

```
L1 violations on round N (cycle <id>):
  ✗ peaked_axis_violation (C1.1: target_axis=llm_only.max_tokens, axis marked PEAKED, no critique rebut)
  ✗ envelope_violation (C1.1: proposed max_tokens=1800 from parent 16384 = -89%, no justification)
  ✗ unjustified_param_mutation (C1.1: critique didn't name max_tokens, runtime_failures empty)
  ✓ schema_compliance (no forbidden-axis or type-mismatch issues)
  ⚠ intra_round_paraphrase (C1.2 ↔ C1.4 Jaccard 0.58 — verify/proof theme)
```

The ✗ / ⚠ / ✓ glyphs make it scannable; the parenthetical names the
specific candidate and citation so the operator can verify in the trace.

## L1 meta-campaign — parallel-use lookup

Companion to [`potter-l1-meta-campaign`](../../.claude/skills/potter-l1-meta-campaign/SKILL.md). The skill ticks, prints a one-paragraph status, exits; this section is what to open **while the next tick is paused** — to verify or debunk what the last tick claimed. SKILL.md is authoritative for what the skill reads; drift = skill wins.

> Project root is `.promptpotter/projects/{project}/` (currently `default`). All `campaigns/…` paths below are relative to that root.

### Driving the loop

Skill = strategist (reads disk, decides, prints the next CLI); `new`/`resume` = executor (produces the cycle artifacts the skill reads). Per SKILL.md the skill never runs `new`/`resume` — the operator executes the printed invocation, then calls the skill again. First run: `python -m promptpotter new {name}`, then `/potter-l1-meta-campaign` (reads `index.json::final.prompt_hashes.{prompt_id}` into `state.json::active_hash`, writes the first `review` row, clears `paused`). Re-invoke after: a full cycle completes (Phase 2 `review` verdict), a sweep batch completes (Phase 4 `screen` rows), or you applied a `proposed_edits/` diff (clear `paused: false` first). Ticks are idempotent and exit fast when nothing is new.

### Files cheatsheet

| File | When |
|---|---|
| `.promptpotter/meta_campaigns/{prompt_id}/state.json` | every tick |
| `.promptpotter/meta_campaigns/{prompt_id}/log.jsonl` | every tick (audit) |
| `.promptpotter/meta_campaigns/{prompt_id}/proposed_edits/*.diff` | pending operator action |
| `campaigns/{cycle_id}/review.md` | post-cycle behavior + L1Stats |
| `campaigns/{cycle_id}/index.json` | post-cycle final + `prompt_hashes` |
| `campaigns/{cycle_id}/.runtime/cache/rounds/round_NNNN.json` | LLM I/O per round |
| `campaigns/{cycle_id}/.runtime/cache/candidates/round_NNNN.json` | OSPs per round |
| `archive/sweeps/{l1_hash}/{dataset}/*.json` | sweep results |

### Per-phase reference

1. **Assess** — mode = `early` (<3 promote_accept), `plateau` (last 3 lifts < `epsilon_plateau=0.02`), `bridge`, `portfolio` (3 accepts on 2 datasets). Grep `log.jsonl::promote_accept` to corroborate.
2. **Per-cycle review** — `round_1_verdict` ∈ {healthy, degraded, broken}; open `review.md` + `index.json`. Degraded sweep auto-rejects; degraded full or broken pauses the skill + writes `proposed_edits/{cycle_id}_{ts}.diff` (apply, clear `paused`).
3. **Slate** — committed payloads `datasets/{focus}/sweep/NN_*.json`; skill drafts land in `sweep/proposed/` (operator moves up to commit). Rules: same `from_cycle_id + from_round=1`, one prompt_id per candidate, distinct rendered templates.
4. **Screen** — `screen` verdict: winner | tied | loser | reject_health | reject_behavior. `top_lift_r1 = max(candidates.composite) − origin.composite` from `round_0001.json::nodes.l1_score`. Winner threshold `parent + epsilon_lift` (default 0.02).
5. **Promote** — `promote_accept | promote_reject`. early/plateau: `rounds_to_95 <= parent` or `final_accuracy > parent + epsilon_lift` (`final_accuracy` = the cycle index's top-level `best_accuracy`); bridge: both datasets win; portfolio: mean lift > 0, no per-dataset regression > `epsilon_regression=0.05`.
6. **Record** — `proxy_lift_corr` = Spearman over paired `screen` + `promote_*` on `prompt_hash + dataset`. `>=0.6` rung_2 (R1 alone); `0.4–0.6` rung_2 + one full-cycle confirm; `<0.4` rung_3 (R1+R2 minimum).

### Sweep verbs

```bash
python -m promptpotter sweep time-to 66 --l1-prompt datasets/_optimizer/variants/l1_v3.json --dataset aime --max-rounds 10
python -m promptpotter sweep round1     --l1-prompts datasets/_optimizer/variants/l1_v3.json,datasets/_optimizer/variants/l1_v4.json --dataset aime --panel-size 6
python -m promptpotter sweep round2     --from-sweep <sweep_id> --top 3
python -m promptpotter sweep rank       --dataset aime --by round1_accuracy --last 10
```

Result JSON: `archive/sweeps/{l1_hash}/{dataset}/{verb}_..._{ts}.json`. Spec: [`../specs/roadmap.md`](../specs/roadmap.md) § Prompt-iteration framework + exit gate. The skill reads these for Phase 4 verdicts + Phase 6 correlation.
