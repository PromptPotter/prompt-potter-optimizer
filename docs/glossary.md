# Glossary — domain terms with canonical implementation pointers

One line per term: definition + the file where it lives (and any
nearby reference doc). Read top-to-bottom for a vocabulary check
before opening a code file.

The list is short on purpose. If a term feels missing, it's probably
not domain language yet — either add it here when the first second
implementation site lands, or rename to a term already on the list.

---

## Loop layers — what generates, what refines, what replans

- **L1** — `l1_generate`: the candidate-mutation layer that emits new
  pipeline_params variants every round. Lives at
  `application/optimization/l1/` (`generate.py`, `score.py`,
  `critique.py`, `execute.py`, `resume.py`, `population.py`,
  `stats.py`). Contract: `promptpotter/CLAUDE.md`.

- **L2** — `l2_context`: the task-framing refinement layer. Fires on
  L1 stall. Writes `OptSearchPoint.task_context`. Cannot mutate
  `pipeline_params`. Implementation:
  `application/optimization/transitions.py::run_layer_transition` (L2
  branch); rule firing in `escalation/firing.py`.

- **L3** — `l3_plan`: the strategic-replan layer. Fires on L2 stall.
  Writes `OptSearchPoint.plan`. Rewrites the framing surface, not the
  next variant. Implementation: same modules as L2.

- **Critique** — L1's per-round LLM self-analysis. `run_l1_critique`
  in `application/optimization/l1/critique.py`. Output dict
  (`summary`, `priority_fix`, `suggested_axes`, `failure_highlights`)
  is the round's narrative compression for next-round L1.

## SearchPoint hierarchy — what the optimizer mutates

- **JobSearchPoint (JSP)** — frozen target-layer spec: pipeline_params
  + rendered prompt. Content-hashable. `domain/search_point.py`.
- **PromptTemplate** — 8-field prompt scheme rendered via
  `compile_prompt()`. `domain/opt_search_point.py`.
- **OptSearchPoint (OSP)** — optimizer state: lineage + L2 context +
  L3 plan + per-individual memory. Projects to JSP for evaluation.
  `domain/opt_search_point.py`.

## Cross-cycle memory — what the digest serves

- **AxisIndex** — axis-keyed digest aggregated across all prior cycles
  of this dataset. Rankings, top values, value trends, exhausted axes,
  failure clusters, dead/discriminating/volatile queries.
  `application/intelligence/indexes/axis.py`.
- **SampleIndex** — per-sample state (hits/misses across cycles).
  `application/intelligence/indexes/sample.py`.
- **ConfigIndex** — per-pipeline-config rollup for de-duplication and
  effect-size estimation. `application/intelligence/indexes/config.py`.

## Hierarchy — sessions / cycles / forks / siblings

- **Session** — operator workspace + per-cycle bundles (state, scoring,
  observability). `application/bootstrap/session.py`.
- **Cycle** — round-loop state container. One cycle per content-hashed
  origin JSP. `application/optimization/cycle.py`.
- **Fork** — a sibling cycle minted from a parent at a specific ledger
  offset via `CycleEventLog.inherit_from`. Three kinds:
  `forks/` (divergence + operator-chosen), `diag/` (diagnostic),
  `sweeps/<batch>/forks/` (sweep-toolkit A/B candidates).
- **Sibling kind** — `root | fork | diag | sweep`. Derived from the
  directory path under `campaigns/`. See
  `infrastructure/store/paths.py::sibling_kind`.

## Round-level vocabulary

- **Round** — one L1 generate → score → critique iteration.
- **Generation** — one round's set of L1 variants (the "individuals"
  in evolution speak).
- **Candidate** — one prompt-SearchPoint variant inside a round.
  NEVER a retrieval-list item — those are `ranked_items`.
- **Sample** — one dataset row. The input-string field is `query`.
  Use `sample` for everything that aggregates over rows
  (`n_samples`, `per_sample`, `SampleProfile`).
- **Query** — the input-string field on a sample. Use `query` *only*
  as a field name; never as a synonym for "sample" elsewhere.
- **Measurement** — one `(SearchPoint, sample) → result` record.
  Lives in the MeasurementArchive (`archive/measurements/`).

## Scoring — facts vs policy

- **Hit** — boolean rank-1 exact match (per-sample classification).
- **Score** — continuous, formula-driven; feeds the optimizer.
  `application/scoring/formula/`.
- **Fitness / composite** — the scorer expression compiled from
  `campaign.json::scoring.per_sample` and `per_round`. Each measurement
  carries `{scorer_id: {score, hit, formula}}`. Rescored on every load.
  `application/scoring/search_point_scorer.py`.
- **score_search_point()** — the single scoring gateway. Every scoring
  call MUST go through it. `application/scoring/search_point_scorer.py`.
- **Round scorer** — the optional `per_round` formula compiled from
  `campaign.json::scoring`. Recompiled on hot-swap. Lives on
  `session.scoring.round_scorer`.

## Elimination + ranking

- **PoBB** — Posterior of Being Best. Bayesian round-level elimination
  rule (ε=0.05, n_min=4). Joint Normal-CLT posterior + Monte Carlo
  argmax. `application/optimization/pobb/elimination.py`.
- **Posterior width** — `1 - max(p_best)`. Operator-visible measure of
  how confidently the leader can be locked in. Surfaced on
  `dashboard.json::current_round.pobb`.
- **Candidate budget allocation** — how the round's query budget is
  spent across the N candidates. Implemented by PoBB. The umbrella
  term — NEVER call this "query ranking."
- **Rasch sort** — two-axis ordering of (sample-difficulty rank,
  candidate-ability rank). `application/intelligence/hard_sample_sorter.py`.
- **Adaptive picker** — the live per-step sample selector
  inside the PoBB loop. Maintains a Gaussian posterior on the
  candidate's latent ability `θ_c` and re-picks every measurement
  under the configured objective. `application/intelligence/adaptive_picker.py`.
- **EIG** — Expected Information Gain, the default adaptive-picker
  objective. The entropy reduction of the hierarchical IRT
  posterior from one measurement: `½·log(1 + w̄·(var_c + se_δ²))`.
  Reads the sample-difficulty SE, so a barely-measured sample
  ranks high; the `se_δ → 0` limit recovers Maximum Fisher
  Information.
- **Decision objective** — the decision-aligned adaptive-picker
  objective: the mutual information between the next outcome and
  the keep/abort verdict `θ_c > θ_s` against the seed. The
  means-known limit recovers Bernoulli Chernoff information.
- **picker_objective** — `campaign.json` field selecting the
  adaptive picker's objective: `"model"` (EIG, default) or
  `"decision"`.
- **EIG snapshot** — descriptive per-sample
  `pick_score.per_sample` on the hard-samples artifact: expected
  information gain at the population-prior ability `N(0, σ_θ²)`.
  Consumed by the webapp dataset table; the live picker uses its
  own per-candidate posterior, not this snapshot.
- **llm_ranking** — a backend node that orders ranked_items per
  sample. Distinct from PoBB, Rasch, and the adaptive picker.
  Currently broken on TermNorm (see CLAUDE.md known issues).

## Escalation + healing

- **Patience** — L1's per-cycle stall budget. Bumps after each
  no-improvement round; resets on improvement.
- **Stall** — N consecutive non-improving rounds for L1, L2, or L3.
  Tracked on `EscalationState.l1_stall_count` etc.
- **Escalation** — the post-round router (`decide_escalation` over
  `DEFAULT_ESCALATION_RULES`) deciding CONTINUE / FIRE_L2 / STOP_*.
  `application/optimization/escalation/`.
- **Wound** — one of the four self-healing channels between layers:
  Wound 1 (L1 validation failure → L2 heal), Wound 2 (mid-eval
  degradation → L2 heal), Wound 3 (L1 self-healing on critique),
  Wound 4 (L2 guard-breach → L3 heal). See
  `docs/developer/self-healing-internals.md`.
- **Guard breach** — programmatic post-parse validator outcome on the
  L2 or L3 LLM output. Fields:
  `OptSearchPoint.wounds.l2_guard_breaches`, `wounds.l3_guard_breaches`.

## Dispatch — info-flow to optimizer prompts

- **INJECTIONS** — the typed registry of every `{{slot}}` an optimizer
  prompt can reference. `application/optimization/dispatch/hub/injections.py`.
- **DispatchHub** — the facade with `fill_l1` (L1 layout-driven) and
  `fill_fixed` (L1_CRITIQUE / L2 / L3 placeholder substitution).
  `application/optimization/dispatch/hub/facade.py`.
- **Bundle** (`InjectionBundle`) — frozen per-call state container
  every renderer reads. Built by `build_bundle(cycle)`.
  `application/optimization/dispatch/hub/bundle.py`.
- **l1_layout** — the L2-authored placeholder layout that drives which
  signals appear in each L1 prompt slot. Read at L1 fill time.
  `domain/l1_layout.py`.
- **Evidence grounding** — every L1 variant declares an
  `evidence_grounding: {field, citation}` naming the panel entry that
  justifies the mutation. Validated by
  `application/optimization/validators/l1_behavior.py`.
- **Meta-prompt** — synonym for "optimizer prompt" (L1/L2/L3/Critique
  LLM template). Field-standard from PromptWizard / DSPy / OPRO.

## Connector / pipeline / overlay

- **Connector** — the bundled shape `{wire adapter, session lifecycle,
  experiment-data extract, ground-truth resolver}` registered under one
  name. Today: TermNorm. `promptpotter/connectors/`.
- **Backend** — a connector's running service (TermNorm's FastAPI is
  the canonical example). Read-only from PromptPotter's perspective —
  we never edit a backend's static config.
- **Pipeline** — the connector's typed node graph self-described via
  `GET /pipeline`. Built into `PipelineSchema` at boot.
  `domain/pipeline_schema.py`.
- **Node** — one step in the pipeline. Has runtime, short_circuit,
  node_type, and `optimizer.param_keys`. NEVER "service" or "building
  block."
- **Overlay** — per-dataset operator delta merged onto each wire
  payload. Lives at `datasets/{name}/pipeline.json::nodes.{name}.config`.
  The sole route for changing a backend tunable.
- **pipeline_params** — nested dict keyed by node
  (`{"llm_only": {"model": …}}`). The canonical optimizer-layer shape.

## Persistence — what writes where

- **CycleEventLog** — the single persistence ingress per cycle. Owns
  `events.jsonl`. `infrastructure/ledger.py`.
- **RunCallbacks** — typed event constructor over
  `CycleEventLog.append`. The writer-side API orchestration uses.
  `application/run_observers.py`.
- **MeasurementArchive** — cross-cycle DB core. Content-addressed.
  Lives at `archive/measurements/`. The optimizer's long-term memory.
  See `infrastructure/store/archive_views.py`.
- **Projection** — a `DerivedView` subclass that subscribes to the
  ledger and writes its own artifact (dashboard.json, audit cache,
  PoBB stream). `infrastructure/projections/`.
- **stop.flag** — `.runtime/stop.flag` under a cycle dir. The webapp's
  "Stop run" writes this; `session.stop_check` polls it. The only
  sanctioned Control-local mutation surface.

## Domain framing words (rule of thumb)

PromptPotter = LLM-driven program evolution. Prefer evolutionary
vocabulary when naming new things:

- **evolve / generation / population / fitness / mutation / selection /
  individual**.
- "Loop / round / searchpoint / sample / measurement / scoring / trial /
  critique" are also load-bearing in current code.

Avoid:

- **eval** — banned from identifiers and prose (`Evaluator` class is
  the only exception). Use loop / round / scoring / fitness.
- **service** / **building block** — say `node`.
- **legacy** in code comments — either the path is dead (delete it)
  or the word is wrong (delete the word).
- **query ranking** — pick the precise name: PoBB, Rasch sort, or
  `llm_ranking`.
