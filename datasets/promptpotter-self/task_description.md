# Task: optimize PromptPotter's meta-prompts

## What the outer cycle does

Mutate the inner PromptPotter cycle's L1 / L1_CRITIQUE / L2 / L3 meta-prompt
template fields (six-field PromptTemplate scheme) to improve inner-cycle
optimization performance on the GSM8K-small proxy benchmark.

## What "good" looks like

A candidate meta-prompt set is better than baseline when its inner cycle
shows higher delta-from-baseline accuracy after N rounds, hits the target
score in fewer rounds, or both. The composite scoring formula in
``campaign.json::scoring`` weights these three proxies in concert:

- ``first_round_delta`` — score after inner round 1 minus inner baseline.
  Cheapest signal, useful for quick iteration on outer hyperparameters.
- ``after_N_rounds_delta`` — score after N inner rounds minus inner
  baseline. The workhorse metric; captures improvement rate.
- ``rounds_to_N`` — number of rounds to reach the inner target score
  (``inner_tasks.json::target_score``); times out at ``max_inner_rounds``.

## Constraints

- The mutation surface is per-meta-prompt template fields (``persona``,
  ``task_intent``, ``problem_description``, ``instruction``,
  ``thinking_style``, ``answer_format``) — these are the keys exposed in
  ``pipeline.json::nodes.{node}.optimizer.param_keys``.
- The four meta-prompt nodes (``l1_generate``, ``l1_critique``,
  ``l2_context``, ``l3_plan``) share the six-field scheme; outer L1 may
  mutate any subset per round.
- The inner cycle's ``pipeline_params`` and inner ``optimizer_llm``
  config are **not** the outer's mutation surface — those belong to the
  inner cycle. Outer mutations stay at the meta-prompt template-field
  level.

## Why this dataset is "small"

The GSM8K subset is tiny (``n_samples_per_inner_round: 10``,
``max_inner_rounds: 3``) so each outer "sample" runs an inner cycle in
order-of-minutes rather than order-of-hours. The trade-off is signal
quality — calibration runs should expand sample count before publication
runs. See ``docs/specs/m12-promptpotter-as-connector.md`` § Cost realism.
