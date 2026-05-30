# Demo — Try & Learn: Support Tickets

The built-in try-and-learn dataset. Surfaced to new users while
`User.demo_mode_enabled` is on (toggle in Account → Preferences); minting
resolves this repo dir directly — nothing is copied into the tenant.

## Type

Single `llm_only` generation node, no retrieval. Classifies a support ticket
into one of five categories against a running TermNorm backend.

## Data

- 15 hand-authored rows, 3 per category (`refund`, `password_reset`,
  `bug_report`, `shipping_status`, `billing_inquiry`)
- The whole set is the train split — small on purpose, so a full optimize loop
  runs in a few rounds.

## Scoring

`exact_match(predicted, ground_truth)` — case-insensitive, whitespace-stripped
string equality. The model must emit only the label.

## Pipeline Notes

- `llm_only` on Groq `openai/gpt-oss-120b`, `reasoning_effort: low` (floored;
  the optimizer may raise it within the allowed set).
- Model + provider are pinned (`forbidden_axes_strict`); the optimizer evolves
  the prompt fields, `temperature`, `max_tokens`, and `reasoning_effort`.
