# HotPotQA — Multi-Hop Question Answering

Answer questions that require reasoning across multiple Wikipedia passages. Each question comes with a set of supporting context passages.

## Domain

- Input: a natural language question + 10 context passages (2 relevant, 8 distractors)
- Output: a short answer string (typically a named entity, date, or short phrase)
- Challenge: identifying which passages are relevant, chaining facts across them

## Success criteria

- Token F1: overlap between predicted and gold answer tokens
- Exact Match: predicted answer exactly matches gold answer (after normalization)

## Key failure modes

- Using distractor passages instead of supporting ones
- Answering from a single passage when the question requires multi-hop reasoning
- Over-verbose answers (hurts exact match)
- Missing the bridge entity that connects the two relevant passages
