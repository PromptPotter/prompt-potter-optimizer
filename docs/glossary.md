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
  `application/optimization/l1/` (`generate.py`, `score/`,
  `critique.py`, `execute.py`, `resume.py`, `population.py`,
  `stats.py`). Contract: `promptpotter/CLAUDE.md`.

- **L2** — `l2_context`: the task-framing refinement layer. Fires on
  L1 stall. Writes `OptSearchPoint.task_context`. Cannot mutate
  `pipeline_params`. Implementation: `escalate_l2` in
  `application/optimization/escalation/firing.py`; per-layer
  parse/apply (`_parse_l2`/`_apply_l2`) in `firing.py`.

- **L3** — `l3_plan`: the strategic-replan layer. Fires on L2 stall.
  Writes `OptSearchPoint.plan`. Rewrites the framing surface, not the
  next variant. Implementation: `escalate_l2`'s L3 cascade in
  `firing.py`; parse/apply (`_parse_l3`/`_apply_l3`) in `firing.py`.

- **Critique** — L1's per-round LLM self-analysis. `run_l1_critique`
  in `application/optimization/l1/critique.py`. Output dict
  (`summary`, `priority_fix`, `suggested_axes`, `failure_highlights`)
  is the round's narrative compression for next-round L1.

## SearchPoint hierarchy — what the optimizer mutates

- **JobSearchPoint (JSP)** — frozen target-layer spec: pipeline_params
  + rendered prompt. Content-hashable. `domain/search_point.py`.
- **PromptTemplate** — 8-field prompt scheme rendered via
  `compile_prompt()`. `domain/opt_search_point.py`. `compile_prompt` fills
  the supplied slots and leaves any other `{{…}}` literal — a node prompt's
  own backend placeholders (`{{query}}`, `{{combined_text}}`) are content,
  not optimizer slots; authored-slot typos are caught at load by
  `dispatch.validate_template`.
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

## Hierarchy — workspace / dataset / campaign / session / unit

The persisted world is a four-entity containment hierarchy
(outermost → innermost). The Session is a unit of a campaign;
`active_session.json` is the operator's pointer/lens to the active one.

- **Workspace** — the tenant-level container and queryable datastore:
  every dataset, every campaign, the shared `archive/`. On disk
  `projects/{tenant}/`.
- **Dataset** — an immutable bank of labeled examples
  (`cache.json::items[]` of `{query, ground_truth}`) plus its origin
  config: starting prompt (`prompts/default.json`), pipeline overlay
  (`pipeline.json`), task framing (`task_description.md`), and the
  sibling default `campaign.json`. Lives at `datasets/{name}/` (repo
  benchmark) or `projects/{tenant}/datasets/{name}/` (tenant upload).
  Data a campaign has touched is **never mutated in place** — see
  **Dataset name**.
- **Dataset name** (a.k.a. **slug**) — the human-friendly handle for a
  dataset (`email-tagging-eval`), validated `^[a-z][a-z0-9_-]*$` and used
  as the directory segment. A *mutable alias*: it points at data, it is
  **not** the data's identity. A campaign resolves its dataset live by
  this name (`campaign.json::dataset_name`), so moving/replacing it is the
  version-and-repoint contract, not an in-place overwrite (the old data
  is preserved under `{slug}-vN`; orchestration in
  `application/datasets/dataset_replace.py`).
- **Origin** — the **complete specification the potter loop starts from**:
  the prompt fields, the per-node pipeline config, the pipeline's
  **required inputs** (query/target column map, answer space, and any
  node-type dependency such as a `candidate_source` node's candidate
  library), and the dataset binding — the *starting program* the optimizer
  evolves from. **Per-pipeline** and **independent of measurement**: it
  exists fully formed *before* anything is scored. Resolved by
  `resolve_origin_opt_search_point` (`application/origin.py`). Reserve this
  word for that meaning; the data a campaign starts from is a **Dataset**,
  not an "origin" — historical UI/spec copy that called the dataset an
  "Origin" was renamed to **Dataset**.
  - **origin's round-0 score** (`origin_accuracy` / round 0 / **C0**) — the
    **measurement** produced by scoring the origin, emitted as round 0. It
    is *downstream of* the origin, **not part of its definition**; say
    "the origin's round-0 score", never equate it with the origin itself.
    Scoring step: `establish_campaign_origin` → `emit_origin_round`.
- **Campaign** — one declared optimization effort: a dataset, a
  pipeline origin, context text, and the optimizer meta-prompts it runs
  under. A first-class entity holding one session root + its fork/diag/
  sweep descendants. Directory `campaigns/{campaign_id}/` +
  `campaign.json` manifest. `campaign_id = {dataset}__{rand6_hex}` —
  minted fresh per `new` call by `mint_campaign_id`; each `new` produces
  a distinct campaign. The declaration (target hash +
  optimizer-prompt hash) is recorded as properties on `campaign.json`
  for drift detection on resume, not as the id. `domain/campaign.py`.
- **Session** — one run of `new` on a campaign. A campaign holds one
  session — the `new` invocation that minted it. `resume` extends that
  session. Identity is the `session_id` (`s_xxxx`). Each session is a
  tree: a root cycle (bare `cycle_<target_hash>`) plus its fork
  descendants. `application/bootstrap/session.py`. Pre-existing on-disk shape
  (multi-session forest with `_s{N}` suffixes): see
  `promptpotter/infrastructure/store/paths.py`.
- **Unit** — one continuous-parameter run inside a session. A session
  starts with one unit; `resume` extends the current unit; each fork
  (human / L3 / divergence) branches a new unit. The operator-facing
  name for a **Cycle** — the webapp + docs say "unit", the on-disk / API
  identifier stays `cycle_id`.
- **Cycle** — the round-loop state container; the internal name for a
  Unit. `cycle_{content_hash[:12]}` from the origin JSP content hash
  (+ `_fork_`/`_diag_`/`_sweep_` for branches). `cycle_id` is
  campaign-scoped — path resolution is `(campaign_id, cycle_id)`.
  `application/optimization/cycle.py`.
- **unit_kind** — operator-facing label on the webapp sidebar, computed
  server-side from `(sibling_kind, fork_trigger)`: `session` (a session
  root run; `resume` extends it), `divergent_resume` (a `resume
  --fork-on-divergence` branch), `user_fork` (any operator-initiated
  branch — HITL fork, diagnostic, sweep, folded into one kind),
  `auto_rebase` (an automatic L2/L3-rebase branch; fork trigger `l2_rebase` / `l3_rebase`).
- **Data scope** — `campaign | dataset | workspace`: the three named
  scopes the Workspace datastore is queried at — one campaign's
  cycles (across every session), every campaign for one dataset, or
  everything. Used identically by the archive query API, heatmap
  artifacts, the `scope` API param, and the webapp toggle.
- **Fork** — a sibling cycle minted from a parent at a specific ledger
  offset via `CycleEventLog.inherit_from`, inside the **same
  session**. Three kinds: divergence (operator-chosen), diagnostic,
  and sweep (sweep-toolkit A/B candidates) — all flat under the
  campaign's `cycles/`.
- **ForkSpec** — the single typed fork record (`domain/run_records.py`),
  the one writer behind three projections: the parent's `FORK_CUT`
  ledger entry (SoT), the fork's `index.json::fork` (lineage read), and —
  when steered — its `.overrides/seed.json` (bootstrap read). Carries
  `{trigger, reason, issued_by, from_round, from_candidate_id, seed}`.
  Absorbs the old free-dict `index.json::fork` + ledger `ForkPayload`.
- **CycleSeed** — the chosen-searchpoint seed a non-root cycle begins from
  (`domain/run_records.py`):
  `{origin_prompt_fields, pipeline_overlay, limit_overrides, origin_source}`.
  Carried by every operator-steered fork's `ForkSpec` (the wire
  `OperatorForkOverride` command payload deserializes into it) AND written by the
  mint seam for campaign-from-origin. The seed prompt becomes the cycle's origin
  `OptSearchPoint` (`resolve_origin_opt_search_point`, lineage stamped from
  `origin_source` — `fork_seed` or `campaign_origin`) and `pipeline_overlay`
  layers onto the dataset overlay (seed > dataset) for that cycle only. The
  dataset file stays immutable. Non-operator triggers (sweep / diag / rebase)
  carry no seed.
- **operator_steered** — the `ForkTrigger` for an operator-initiated fork:
  the operator picks a searchpoint, edits its prompt / node config / run
  limits, and forks from it (a `CycleSeed` is required). Restarts round
  numbering at 1. Replaced the free-string `operator_hitl`. Queryable in the
  lineage tree (`lineage.py`).
- **Cycle override store** — `CycleOverrideMixin` (`store/campaign_store/`)
  writes/reads `cycles/{id}/.overrides/seed.json`, the **read-once**
  per-cycle override home (a steered fork's / campaign-origin's `CycleSeed`), distinct from
  `.runtime/{stop,pause,spend_cap}` which are **polled** per round. The
  dir name encodes read-cadence; the seed is read at the single runner
  seam (`runner/entry.py::run_optimization`) keyed by the fork `cycle_id`.
- **Sibling kind** — `root | fork | diag | sweep`. Recorded in the
  cycle's `index.json` metadata, not derived from a directory path.
  See `infrastructure/store/paths.py::sibling_kind`.
- **CycleDir** — the domain newtype for a cycle's own dir
  (`campaigns/{campaign_id}/cycles/{cycle_id}`). Both the per-cycle
  `dashboard.json` and the audit tree bind here — every cycle (root, fork,
  sweep, diag) owns its own live file, stamped with its own `cycle_id`.
  `domain/cycle_paths.py`.

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
  argmax. `application/optimization/pobb/elimination/checks.py`.
- **Posterior width** — `1 - max(p_best)`. Operator-visible measure of
  how confidently the leader can be locked in. Surfaced on
  `dashboard.json::current_round.pobb`.
- **Candidate budget allocation** — how the round's query budget is
  spent across the N candidates. Implemented by PoBB. The umbrella
  term — NEVER call this "query ranking."
- **Rasch sort** — two-axis ordering of (sample-difficulty rank,
  candidate-ability rank). `application/intelligence/hard_sample_sorter.py`.
- **ability (θ_c)** — a candidate's *difficulty-adjusted* quality on a
  latent logit scale: clearing a hard sample is worth more than clearing
  an easy one. The metric the round winner is elected on and PoBB
  eliminates on — **subset-invariant**, so two candidates scored on
  different sample sets still compare fairly (unlike raw accuracy).
  `application/intelligence/exploration.py::fit_rasch` → `RaschPosterior.theta`;
  consumers in `scoring/metrics.py` (`elect_round_winner` / `elimination_p_best`).
  Operator-facing: [`methods/exploration-exploitation.md`](methods/exploration-exploitation.md).
- **difficulty (δ_s)** — a sample's hardness on the same logit scale; the
  hard-samples leaderboard. Same fit, the other axis. `RaschPosterior.delta`.
- **1PL / Rasch** — the one-parameter logistic IRT model
  `P(hit) = σ(θ_c − δ_s)` (difficulty only). The default model; every cold or
  non-discriminating dataset stays here.
- **2PL** — adds a per-sample **discrimination** `a_s` (signal-to-noise:
  how sharply a sample separates able from unable candidates):
  `P(hit) = σ(a_s·(θ_c − δ_s))`. The difficulty ruler **graduates** 1PL→2PL
  per-dataset (`graduate_ruler_model`) only where a data-rich dataset wins
  held-out cross-validation — so it never regresses a dataset. The richer
  `(δ, a)` value rides inside the one ruler, so the switch is invisible above
  `fit_theta_given_delta`. Operator knob `enable_2pl_graduation`. Shipped —
  slice 3 of [`specs/fitness-comparability.md`](specs/fitness-comparability.md).
- **specific objectivity** — the Rasch property that makes θ comparable
  across candidates measured on *different* subsets. The reason θ gates
  instead of subset accuracy. Same spec.
- **estimand (config sense)** — the statistical quantity an optimization
  knob *moves*: the scored subset, the difficulty ruler δ, the ability θ,
  the gate, the stopping rule, … The axis the config map groups knobs by;
  knobs sharing an estimand are the ones that can collide.
  `application/config_coupling.py::Estimand`.
- **config coupling / config map** — the declared registry of which knob
  moves which estimand and which knobs *clash* (a combination that makes a
  shared estimand ill-defined or a tuned knob inert). One source of truth
  (`application/config_coupling.py`), read by the pre-run preflight warning,
  the `python -m promptpotter.diagnostics.config_map` table, and the webapp
  Config-map panel. Answers "what overwrites what" (provenance: effective
  value + source layer per knob) and "what clashes with what" (the active
  couplings). Where the "deferred-with-the-flip" interactions in
  [`specs/fitness-comparability.md`](specs/fitness-comparability.md) became
  machine-checked.
- **Adaptive queue mechanism** — the live per-step sample selector
  inside the PoBB loop. Maintains a Gaussian posterior on the
  candidate's latent ability `θ_c` and re-picks every measurement
  by the pick-value objective.
  `application/intelligence/adaptive_queue_mechanism.py`.
- **pick-value** — the adaptive queue mechanism's single one-step-greedy
  objective: `decision_information_gain` (in nats). Drives the
  per-step pick and the `pick_score` snapshot. (The earlier blended
  `+ explore_weight · model_information_gain` term was dropped 2026-05;
  see [`specs/verdict-resolution.md`](specs/verdict-resolution.md).)
- **Decision information gain** — the pick-value objective: the
  mutual information between the next outcome and
  the keep/abort verdict `θ_c > θ_s` against the seed. The
  means-known limit recovers Bernoulli Chernoff information.
- **pick_score snapshot** — descriptive per-sample
  `pick_score.per_sample` on the hard-samples artifact: the
  pick-value for a fresh mutation of the seed, ability
  prior `N(θ_seed, σ_θ²)` (centred on the parent, not the
  population-mean anchor 0). Consumed by the webapp dataset table;
  the live adaptive queue mechanism uses its own per-candidate
  posterior, not this snapshot.
- **llm_ranking** — a backend node that orders ranked_items per
  sample. Distinct from PoBB, Rasch, and the adaptive queue mechanism.
- **prediction (terminal ranking)** — the per-sample `predicted` is the
  head of the **terminal ranker's** ranked output (`candidate_ranking`
  for a `token_matching`-terminal pipeline, `final_ranking` for an
  `llm_ranking`/`llm_only` one), derived by `terminal_ranking()` — the
  pipeline shape decides the source key, it is not hardcoded.
  `application/optimization/pobb/elimination/classification.py`.

## Escalation + healing

- **Patience** — L1's per-cycle stall budget. Bumps after each
  no-improvement round; resets on improvement.
- **Stall** — N consecutive non-improving rounds for L1, L2, or L3.
  Tracked on `EscalationFSM.l1_stall_count` etc.
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
  prompt can reference. `application/optimization/dispatch/hub/injections/registry.py`.
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
- **checkin** — the fifth optimizer node (**renamed from `restructure`**,
  commit `269e9b87` — that old name is gone from the code). One node, two
  modes sharing `CheckinOutput`
  (`application/optimization/dispatch/schemas.py`): **task decomposition**
  (raw `task_description` → the 8-field prompt; CLI `new`,
  `task_context.py::decompose_prompt_fields`) and **origin resolution**
  (draft origin → findings/recap; web ingest,
  `datasets/origin_resolve.py`). Not a loop layer — runs via
  `run_optimizer_node`, not the dispatch bundle. Full contract:
  `application/optimization/CLAUDE.md § checkin`.

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
- **pipeline_params** — node-keyed config dicts plus the reserved `steps`
  list of active node names (`{"steps": [...], "llm_only": {"model": …}}`).
  The canonical optimizer-layer shape; never a flat `{param: value}` map.

## Persistence — what writes where

- **CycleEventLog** — the single persistence ingress per cycle. Owns
  `events.jsonl`. `infrastructure/ledger.py`.
- **RunCallbacks** — typed event constructor over
  `CycleEventLog.append`. The writer-side API orchestration uses.
  `application/run_observers.py`.
- **MeasurementArchive** — cross-cycle DB core. Content-addressed.
  Lives at `archive/measurements/`. The optimizer's long-term memory.
  `infrastructure/store/measurement_archive.py`; facade functions in
  `infrastructure/store/archive_views.py`.
- **Projection** — a `DerivedView` subclass that subscribes to the
  ledger and writes its own artifact (dashboard.json, audit cache,
  PoBB stream). `infrastructure/projections/`.
- **pause.flag** — `.runtime/pause.flag` under a cycle dir. The webapp's
  "Pause run" writes this; `session.pause_check` polls it. The single
  operator-interrupt flag (no separate `stop.flag`): the worker exits
  cleanly at the next checkpoint and the cycle stays resumable. The only
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
