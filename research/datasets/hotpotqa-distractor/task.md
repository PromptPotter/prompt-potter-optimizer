---
domain: Multi-hop question answering (HotpotQA, distractor setting)
pipeline_purpose: >-
  Produce the answer to a multi-hop question from the ten paragraphs given with it; two hold the facts,
  eight are distractors.
data_characteristics: >-
  The input is one English question that chains two Wikipedia facts (a bridge entity) or compares two
  entities, followed by ten titled paragraphs. The gold answer is a span of a supporting paragraph as
  written there, or yes / no for a comparison question.
optimization_goals: >-
  Maximize Exact Match after SQuAD normalization: the last bold span, lowercased with punctuation and
  articles removed and whitespace collapsed, must equal the gold span.
key_challenges: >-
  Find both supporting paragraphs among the distractors, resolve the bridge rather than answering from the
  first entity alone, and give the answer span exactly as the gold has it.
upstream_context: ''
downstream_context: >-
  The grader reads only the last `**…**` bold span in the response, SQuAD-normalizes both sides, and
  compares them for equality.
---
# HotpotQA — multi-hop QA, distractor setting

Answer a multi-hop factoid question from the ten paragraphs given with it.

## Domain

- Input: one question and ten titled paragraphs (two supporting, eight distractors)
- Output: the answer span — an entity, a date, a number, a phrase, or `yes` / `no` for comparison questions
- Challenge: a fact in one paragraph identifies the entity a second paragraph is about (bridge questions), or facts about two entities must be compared

## Success criteria

- Exact Match after SQuAD normalization: the last `**…**` bold span, lowercased, with punctuation and a/an/the removed and whitespace collapsed, equals the gold span

## Key failure modes

- Answering from a distractor paragraph about a similarly named entity
- Answering about the first entity without resolving the bridge
- A bold span that differs from the gold span
