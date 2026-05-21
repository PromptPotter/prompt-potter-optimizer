# JustLogic — 3-Class Deductive Reasoning at Depth 6-7

Evaluate a claim against a paragraph of premises and decide whether
the claim is provably true, provably false, or genuinely uncertain.
Synthetic deductive reasoning (Chen 2025) — knowledge-independent
by design, no factual recall required.

## Domain

- Input: 3-7 short logical premises (English prose, plain language)
  + a single claim to evaluate
- Output: one of three labels — `TRUE`, `FALSE`, or `Uncertain`
- Wired stratum: **depths 6 and 7 only** (the hardest two depths
  of the authors' depth-1-through-7 schema; recon-measured at ~44%
  origin on `gpt-oss-20b @ low`)

## Success criteria

- Exact Match: after stripping the last `**…**` bold span, the
  predicted label equals the gold label (case-insensitive)
- The grader reads only the last bolded span — the model must commit
  with `**TRUE**` / `**FALSE**` / `**Uncertain**` at the end of its
  response

## Key failure modes

The dataset's per-depth label distribution is balanced at ~33.3%
each (TRUE/FALSE/Uncertain), so any class-bias the model exhibits is
a real reasoning failure, not a label-skew artifact.

- **Hedge bias toward `Uncertain`** (the dominant failure on `gpt-oss-20b @ low`):
  recon measured 17/25 preds = `uncertain` against an actual 40%
  ground-truth share. The model defaults to "uncertain" when the
  reasoning chain is long, even when the premises strictly determine
  the conclusion. **This is the primary L1 attack surface.**
- **Premise-skipping**: model latches onto one or two premises and
  ignores the rest; produces a confident TRUE/FALSE on insufficient
  evidence
- **Negation-flip**: model loses track of negations in nested
  conditional premises ("if not X, then …") and inverts the
  conclusion
- **Quantifier confusion**: model confuses "all" / "some" / "no" in
  multi-premise chains
- Format-level: emits prose instead of `**LABEL**` — grader
  extracts nothing

## Notes

- Reasoning depths 6-7 correspond to argument chains of 6-7 logical
  steps from premises to conclusion. Llama3-8B canonical accuracy on
  the full dataset = 57.8%; o1-preview = 81%; human avg = 73%. Our
  44% on the hardest stratum is well within the expected band for a
  20B model at low reasoning effort.
- Synthetic generation = no contamination concern. The premises and
  conclusions are programmatically constructed (see paper §3) — the
  model cannot have memorized them.
- 3-class deterministic grading means the recon's 44% is a true
  reasoning measurement, not inflated by template recognition or
  label memorization.

## Constraints

- The target model (`llm_only.model`) is pinned to
  `openai/gpt-oss-20b:nitro` (see `datasets/justlogic/pipeline.json::available_models`).
  Provider pinned to `openrouter`. L1 must not propose `model`,
  `provider`, or `reasoning_effort` mutations. `model` and `provider`
  are operator-locked axes (`PARAM_FORBIDDEN_KEYS`); `reasoning_effort`
  is pinned to `low` for this campaign
  (`pipeline.json::nodes.llm_only.optimizer.param_allowed_values`) — a
  `medium`/`high` proposal is rejected as invalid and self-healed.
- L1 may freely mutate: `temperature`, `max_tokens`, and any prompt
  field (`persona`, `task_intent`, `problem_description`,
  `instruction`, `thinking_style`, `answer_format`). The hedge bias
  toward `Uncertain` must be broken by prompt engineering, not by
  buying reasoning compute — the hedge-breaking prompt mutation is the
  highest-EV target.
