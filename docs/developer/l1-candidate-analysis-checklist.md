# L1 candidate analysis checklist

Load this when reviewing an L1 round trace — operator pastes a CLI dump,
asks "what does this tell you?", or you're acting on a round's behavior
checks. Walk every item below
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
- Any `field` ⇒ the panel must be one the round's layout actually
  rendered (`citable_fields`). A citation naming an absent panel is a
  fabrication, and the prompt no longer offers it as an option.

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
| Forbidden axes (`model`, `provider`) | `validate_overrides()` (always locked) |
| Re-propose known-failing config | `L1_CONFIG_NOT_IN_RUNTIME_FAILURES` (this PR) |
| L3 plan length floor / verbatim repeat | `L3_PLAN_LENGTH_FLOOR`, `L3_PLAN_VERBATIM_REPEAT` |

(Two rows naming L2 task_context validators used to sit here under that *today*.
They were never written, and the surface they policed is gone — L2 cannot write
`task_context` at all. L2's framing output is checked by `l2_behavior.py`.)

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
