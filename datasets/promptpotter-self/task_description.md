# Task: evolve PromptPotter's own meta-prompts

Goal: the outer cycle mutates the **inner** cycle's L1 / L1_CRITIQUE / L2 / L3
meta-prompt template fields (6 per node × 4 nodes = 24 fields) so inner cycles
converge **faster and further** on the proxy benchmark.

## What the inner cycle is solving (so you mutate toward the right behaviour)

The inner benchmark is **justlogic deductive reasoning** (NOT
arithmetic; the specific depth mix is the dataset's, in `inner_tasks.json` — not
restated here, where a stale number would only anchor you). Each inner sample is
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
Mutate the inner meta-prompt fields to make the inner *optimizer* better at
finding the discipline; do not hard-code task answers into it.

## Fitness

Composite formula in ``campaign.json::scoring`` — lift × quality × efficiency:

- ``after_N_rounds_delta`` — the lift core: the inner search's MEAN ability across
  its rounds, minus where it started, in logits on one difficulty-adjusted ruler
- ``cleanliness × diversity_health`` — bounded quality modulator
- ``delta_per_dollar`` — efficiency modulator

Better = a whole trajectory held high, cleanly and cheaply won — not one lucky
round. Because the core averages, sustaining a gain beats spiking to it and
falling back, and a late collapse costs you the rounds it ruins.
(``first_round_delta`` measures round 1 alone. Emitted, held out of the formula:
two candidates' evidence is mostly noise at this sample budget.)

## Mutation surface

- Per-node six-field PromptTemplate scheme: ``persona``, ``task_intent``,
  ``problem_description``, ``instruction``, ``thinking_style``, ``answer_format``
- Exposed at ``pipeline.json::nodes.{node}.optimizer.param_keys``
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

The committed inner config (``inner_tasks.json`` — the source of truth for the
inner geometry; don't restate its numbers here) keeps each outer "sample" at
order-of-minutes. Trade-off is signal quality — bump sample count + rounds before
publication runs. Cost shape + the finish-line plan:
``docs/specs/l4-outer-loop.md`` § Finish line.
