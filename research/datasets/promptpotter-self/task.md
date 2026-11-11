---
domain: Prompt engineering and self-optimization for LLM pipelines
pipeline_purpose: >-
  Automatically evolve PromptPotter's optimizer prompt templates so the inner search gains sooner on
  every benchmark of the panel (the seats are dataset.yaml::panel's).
data_characteristics: >-
  Each sample is a whole inner run; the mutation surface is four prose fields per inner node plus per-node
  panel layouts, and fitness is noisy numeric deltas from a small inner panel, so edits move the score but
  improvement is real and infrequent.
optimization_goals: >-
  Maximize held_out_lift (the MEAN, over inner rounds, of the accuracy of the prompt the inner
  search had ADOPTED by that round on HELD-OUT rows it never searched, minus the origin prompt's
  accuracy on the same rows). The score is the area under the inner curve: a lift bought in
  round 1 counts in every later round, so it is worth more than the same lift bought in round 4,
  and a promotion that only held on the rows the search selected on scores zero. So winning
  edits make the inner optimizer find GENERAL fixes to its own observed failures FASTER, reading
  from its critique and transcripts.
key_challenges: >-
  Outer candidates drift into a single recycled hypothesis instead of grounding each edit in the specific
  inner failure the critique and transcripts show; noisy fitness from a small inner panel makes that drift
  easy.
---
# Task: evolve PromptPotter's own optimizer prompts

Goal: the outer cycle mutates the **inner** cycle's L1 / L1_CRITIQUE / L2 / L3
optimizer prompts (four prose fields per inner node plus per-node panel layouts) so inner cycles
gain **sooner** on the proxy benchmarks.

## What the inner cycle is solving (so you mutate toward the right behaviour)

The panel spans three inner benchmarks (the seats are `panel/dataset.yaml::panel`'s —
not restated here, where a stale list would only anchor you):

- **justlogic deductive reasoning**: premises plus a candidate conclusion, answered
  **TRUE / FALSE / Uncertain** — TRUE/FALSE when the premises strictly determine
  the conclusion, `Uncertain` only when they are genuinely indeterminate. Its
  ground-truth labels include genuine `Uncertain` cases, so an edit that
  suppresses one answer class there trades one error class for another.
- **HotpotQA distractor**: a multi-hop question over ten paragraphs, two of them
  gold, answered with a short span graded by exact match.
- **LegalBench function-of-decision-section**: the role a paragraph of a US court
  opinion plays, answered with one label.

An optimizer-prompt edit is scored on all three at once, so an edit that helps one
task family and hurts another reads as the mixture it is: write edits about how the
inner optimizer searches, never about one task's answers. Each task is hard for its
inner model *as currently prompted*, which is the point: a task it looks bad at is a
task it has not been tuned for yet, not a task with a low ceiling. Assume the room
to improve is large. The goal is to improve, not to reach any particular number —
there is no target score, and nothing here counts rounds to one.

Do NOT assume a failure mode — read one from the evidence. The critique and
sample transcripts show what the inner loop actually did (which candidates it
generated, what its critique diagnosed, where its rounds stalled); each outer
candidate must name the observed inner deficiency it attacks. Mutate the inner
optimizer prompt fields to make the inner *optimizer* better at finding the
discipline; do not hard-code task answers into it.

## Fitness

ONE number, in ``family.yaml::campaign.scoring``:

- ``held_out_lift`` — the mean, over the inner rounds, of the accuracy of the
  prompt the search had ADOPTED by that round on HELD-OUT rows no inner node ever
  read, minus the origin prompt's accuracy on the same rows. Accuracy points,
  linearly re-anchored into [0,1]. The search split's own θ lift
  (``mean_round_delta``) is reported beside it wherever the inner series
  differences, and scores nothing; so do an inner run's seconds and dollars.

Better = the inner optimizer adopted a GENERALLY stronger prompt EARLY and kept it.
The mean is the area under the inner curve, so a search that flatlines for three
rounds then jumps scores below one that climbed steadily to the same place; a
promotion that fits the rows it was selected on and fails fresh ones scores zero.
There is no quality modulator and no efficiency modulator: a campaign whose
optimizer aborted it or spoiled every proposal it made is floored, and a collapsed
arm is eliminated — both structurally, before scoring. So do not optimize for
looking tidy or cheap; optimize for the inner search climbing sooner on rows it has
not seen.

## Mutation surface

- Per-node prose fields: ``persona``, ``task_intent``, ``instruction``,
  ``thinking_style`` — plus a panel ``layout`` on the nodes that declare one
- Four nodes: ``l1_generate``, ``l1_critique``, ``l2_context``, ``l3_plan``.
  Outer L1 may mutate any subset per round
- ``problem_description`` and ``answer_format`` carry the injection slots and
  output contract, so they are not levers
- Out of scope (belongs to inner cycle): inner ``pipeline_params``,
  the inner optimizer's node models

## Intuition (don't bake in — algo should rediscover)

- Dimensionality is non-uniform across the fields:
  - bool / categorical slot → doubles state space
  - free-prose slot (``instruction``, ``thinking_style``, ...) →
    explodes it → more signal AND more noise per round
- Corollary: L2 / L3 shouldn't read early variance on a high-dim slot
  as "axis unstable, avoid" — that's the slot doing what high-dim
  slots do

## Proxy realism

The committed inner config (``panel/dataset.yaml::panel`` — the source of truth for the
inner geometry; don't restate its numbers here) keeps each outer "sample" at
order-of-minutes. Trade-off is signal quality — bump sample count + rounds before
publication runs.
