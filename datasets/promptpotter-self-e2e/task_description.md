# Task: evolve PromptPotter's own optimizer prompts

Goal: the outer cycle mutates the **inner** cycle's L1 / L1_CRITIQUE / L2 / L3
optimizer prompt template fields (6 per node × 4 nodes = 24 fields) so inner cycles
converge **faster and further** on the proxy benchmark.

## What the inner cycle is solving (so you mutate toward the right behaviour)

The inner benchmark is **justlogic deductive reasoning** (NOT
arithmetic; the specific depth mix is the dataset's, declared in
`inner_tasks.yaml`). Each inner sample is
a set of premises plus a candidate conclusion;
the inner model must answer **TRUE / FALSE / Uncertain** — TRUE/FALSE when the
premises strictly determine the conclusion, `Uncertain` only when they are
genuinely indeterminate. The task is hard for the inner model *as currently
prompted*, which is the point: a task it looks bad at is a task it has not been
tuned for yet, not a task with a low ceiling. Assume the room to improve is
large. The goal is to improve, not to reach any particular number — there is no
target score, and nothing here counts rounds to one.

Do NOT assume a failure mode — read one from the evidence. The critique and
sample transcripts show what the inner loop actually did (which candidates it
generated, what its critique diagnosed, where its rounds stalled); each outer
candidate must name the observed inner deficiency it attacks. Note the label
space is three-way and ground-truth labels include genuine `Uncertain` cases —
an edit that suppresses one answer class trades one error class for another.
Mutate the inner optimizer prompt fields to make the inner *optimizer* better at
finding the discipline; do not hard-code task answers into it.

## Fitness

ONE number, in ``campaign.yaml::scoring``:

- ``mean_round_delta`` — the mean, over the inner rounds, of the ability the
  parent that round ADOPTED, minus where the search started; in logits on one
  difficulty-adjusted ruler. Linearly re-anchored into [0,1].

Better = the inner optimizer adopted a stronger prompt EARLY and kept it. The mean
rewards the shape a healthy search has — most cells lift in round 1, about half again
in round 2, the stragglers land in round 3, thinning as they saturate — so a search
that flatlines for three rounds then jumps scores below one that climbed steadily to
the same place. There is no quality modulator and no efficiency modulator: a campaign
that breaks its own measurement is floored, and a collapsed arm is eliminated — both
structurally, before scoring. So do not optimize for looking tidy or cheap; optimize
for the inner search climbing sooner.

## Mutation surface

- Per-node six-field PromptTemplate scheme: ``persona``, ``task_intent``,
  ``problem_description``, ``instruction``, ``thinking_style``, ``answer_format``
- Exposed at ``pipeline.yaml::nodes.{node}.optimizer.param_keys``
- Four nodes: ``l1_generate``, ``l1_critique``, ``l2_context``, ``l3_plan``.
  Outer L1 may mutate any subset per round
- Out of scope (belongs to inner cycle): inner ``pipeline_params``,
  inner ``optimizer_llm``

## Intuition (don't bake in — algo should rediscover)

- Dimensionality is non-uniform across the 24 fields:
  - bool / categorical slot → doubles state space
  - free-prose slot (``instruction``, ``thinking_style``, ...) →
    explodes it → more signal AND more noise per round
- The ``answer_format`` field is highest-leverage AND easiest-to-break: the
  inner scorer reads the last ``**TRUE**`` / ``**FALSE**`` / ``**Uncertain**``
  bold span, so a mutation that drops or garbles that contract makes the inner
  cycle score zero. Expect the same shape as a structured-output schema slot.
- Corollary: L2 / L3 shouldn't read early variance on a high-dim slot
  as "axis unstable, avoid" — that's the slot doing what high-dim
  slots do

## Proxy realism

The committed inner config (``inner_tasks.yaml`` — the source of truth for the
inner geometry; don't restate its numbers here) keeps each outer "sample" at
order-of-minutes. Trade-off is signal quality — bump sample count + rounds before
publication runs. Cost shape + what remains:
``docs/specs/l4-outer-loop.md`` § Cost + § Open.
