# Task: evolve PromptPotter's own meta-prompts

Goal: outer cycle mutates inner cycle's L1 / L1_CRITIQUE / L2 / L3
meta-prompt template fields (6 per node × 4 nodes = 24 fields) so inner
cycles converge faster / further on GSM8K-small as proxy benchmark.

## Fitness

Composite formula in ``campaign.json::scoring`` — three proxies:

- ``first_round_delta`` — inner-round-1 score minus inner baseline (cheap signal)
- ``after_N_rounds_delta`` — inner-round-N score minus baseline (workhorse)
- ``rounds_to_N`` — rounds to hit ``inner_tasks.json::target_score``, capped at ``max_inner_rounds``

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
- TermNorm-side analogy: structured-output schema slots tend to be both
  highest-leverage AND easiest-to-break (cf. ``entity_profile`` JSON
  schema there). Expect similar shape on this surface
- Corollary: L2 / L3 shouldn't read early variance on a high-dim slot
  as "axis unstable, avoid" — that's the slot doing what high-dim
  slots do

## Proxy realism

GSM8K-small (``n_samples_per_inner_round: 10``, ``max_inner_rounds: 3``)
keeps each outer "sample" at order-of-minutes rather than -hours. Trade-off
is signal quality — bump sample count before publication runs. See
``docs/specs/m12-promptpotter-as-connector.md`` § Cost realism.
