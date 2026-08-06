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

## C. Candidates against the standing constraints of `l1_generate/1`

The generator's own prompt is the single owner of what a candidate may
propose — PEAKED-axis discipline, param-field axes as a last resort, the
numeric envelopes. **Read those constraints off
`promptpotter/assets/optimizer/pipeline.yaml::resolved_prompts['l1_generate/1']`
at review time, never off this page:** it is an L4-searched surface, so a
constraint quoted here is a constraint that has already moved.

None are validator-enforced. The check is the same shape for each: for
every candidate that crosses one, read its `evidence_grounding.citation`
and ask whether the evidence the prompt demands is actually quoted there.
If it is not, flag it by the constraint's own name in the analysis report.

## F. Intra-round paraphrase / mode collapse

Mode-collapse is when N candidates push the same conceptual mutation
across different axes (e.g. C1.1 puts "verify" in instruction, C1.2
puts "cross-check" in persona, C1.3 puts "validate" in thinking_style
— all the same idea, different slots).

Not enforced by any validator — there is no L1-side re-proposal check, and no
L2-side one to borrow from (see the note under the validator table below). The
operator could request one as a follow-up.

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

The enforced set is the registry itself —
`application/optimization/validators/l1_strict.py` — plus `validate_overrides()`,
which locks the forbidden axes (`model`, `provider`) unconditionally. Read the
registry before assuming a check is unenforced; a table here goes stale the
first time one is added.

(There are no L2 `task_context` validators: L2 cannot write `task_context` at all —
`TaskDecomposition.merge` refuses it — so the breach is not representable. L2's framing
output is checked by `l2_behavior.py`.)

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
