# JustLogic — 3-Class Deductive Reasoning (depths 2-4 mix)

Evaluate a claim against a paragraph of premises and decide whether the claim is provably true,
provably false, or genuinely uncertain. Synthetic deductive reasoning (Chen 2025) —
knowledge-independent by design, no factual recall.

## Domain

- Input: a paragraph of logical premises (English prose) + a single claim
- Output: one of three labels — `TRUE`, `FALSE`, or `Uncertain`
- Bank: an **iid random mix of depths 2, 3, and 4** of the authors' depth-1-through-7 schema

## Success criteria

- Exact Match: the predicted label equals the gold label (case-insensitive), read from the
  final answer span
- The model must commit to one of the three labels in the expected format — prose with no
  extractable label scores zero regardless of the reasoning

## Key failure modes

- **Premise-skipping**: latches onto one or two premises, ignores the rest, answers confidently
  on insufficient evidence
- **Negation-flip**: a negation inside a nested conditional ("if not X, then …") is lost and the
  conclusion inverts
- **Quantifier confusion**: "all" / "some" / "no" get conflated across a chain
- **Retreat to `Uncertain` on an unfinished chain**: the model answers `Uncertain` not because
  the premises leave the claim open but because it did not finish deriving. **This does not
  respond to being told off** — anti-hedging instructions, derivation procedures and personas
  tend to make it retreat MORE, because the extra text competes for the same budget the
  derivation needs. Help the model close the chain; do not lecture it about the conclusion.
- **Format-level**: no extractable label — the grader reads nothing

## Notes

- Synthetic generation = no contamination; premises and conclusions are programmatically
  constructed (paper §3). Deterministic 3-class grading = a true reasoning measurement, not
  template recognition or label memorisation.

## Constraints

- Target model / provider / reasoning effort are operator-locked (see `pipeline.json`) — the
  point is what a PROMPT buys at fixed capability, not buying reasoning compute.
- Freely mutable: `temperature`, `max_tokens`, and any prompt field (`persona`, `task_intent`,
  `problem_description`, `instruction`, `thinking_style`, `answer_format`), plus the
  structured-output field descriptions.
