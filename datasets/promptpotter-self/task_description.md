# Task: evolve PromptPotter's own meta-prompts

Goal: the outer cycle mutates the **inner** cycle's L1 / L1_CRITIQUE / L2 / L3
meta-prompt template fields (6 per node × 4 nodes = 24 fields) so inner cycles
converge **faster and further** on the proxy benchmark.

## What the inner cycle is solving (so you mutate toward the right behaviour)

The inner benchmark is **justlogic, depth-6/7 deductive reasoning** (NOT
arithmetic). Each inner sample is a set of premises plus a candidate conclusion;
the inner model must answer **TRUE / FALSE / Uncertain** — TRUE/FALSE when the
premises strictly determine the conclusion, `Uncertain` only when they are
genuinely indeterminate. Origin ≈ 0.44, target 0.60, paper ceiling ≈ 0.81.

The dominant failure mode is **hedge bias**: the inner model over-uses
`Uncertain` to avoid committing. So the inner meta-prompts that win are the ones
that make the inner L1 generate candidates which **break the hedge** — commit to
TRUE/FALSE under strict entailment, scaffold premise-by-premise checking, and
reserve `Uncertain` for real indeterminacy. Mutate the inner meta-prompt fields
to push the inner loop toward discovering that discipline; do not hard-code the
answer — make the inner *optimizer* better at finding it.

## Fitness

Composite formula in ``campaign.json::scoring`` — three proxies:

- ``first_round_delta`` — inner-round-1 score minus inner origin (cheap signal)
- ``after_N_rounds_delta`` — inner-round-N score minus origin (workhorse)
- ``rounds_to_N`` — rounds to hit ``inner_tasks.json::target_score`` (0.60), capped at ``max_inner_rounds``

Better = higher delta after N AND/OR fewer rounds to target.

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

The committed inner config (``inner_tasks.json``: ``n_samples_per_inner_round:
8``, ``max_inner_rounds: 2``, two seeds) keeps each outer "sample" at
order-of-minutes. Trade-off is signal quality — bump sample count + rounds before
publication runs. Cost shape + the finish-line plan:
``docs/specs/l4-outer-loop.md`` § Finish line.
