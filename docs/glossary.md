# Glossary — domain terms with canonical implementation pointers

One line per term: definition + the file where it lives (and any
nearby reference doc). Read top-to-bottom for a vocabulary check
before opening a code file.

The list is short on purpose. If a term feels missing, it's probably
not domain language yet — either add it here when the first second
implementation site lands, or rename to a term already on the list.

---

## Same word, two referents — check before you grep

Bare words that name more than one live thing. This section exists because the
expensive mistake is never "couldn't find it" — it is "found the wrong one."

- **`output_schema`** — (1) the **target** node's wire schema: what a backend node
  is asked to return. `domain/pipeline_schema.py::NodeOutputSchema`, edited at
  `datasets/{name}/pipeline.json::nodes.{node}.config.output_schema`. (2) the
  **optimizer's own** response schema: what `l1_generate` returns, built by
  `validators/l1_strict.py::build_l1_response_schema`. The L4 levers named
  `output_schema_*` act on (2). Sense (1) is where a "describe the fields" axis
  belongs; building it against (2) is the mistake this section was written for.
- **`seed`** — three senses. (1) **`CycleSeed`** — the chosen starting point a
  non-root cycle begins from (`domain/run_records.py`). (2) the **incumbent
  candidate** a round measures against (`seed-MISS` stratum, `θ_seed`,
  `domain/results.py`). (3) an **RNG integer** on a node's wire config
  (`cfg["seed"]` on a node's `pipeline.json` config). Only (1) is a fork concept.
- **`campaign.json`** — two incompatible schemas, one filename. Under
  `datasets/{name}/` it is the **template** (a `campaign_config` wrapper, read by
  `application/datasets/authored.py`). Under `campaigns/{id}/` it is the minted
  **manifest** (a frozen `Campaign`, `extra="forbid"`, owned by `CampaignStore`).
  Check which tree the path is under before assuming a shape.
- **`"llm_only"`** — the **single-node pipeline sentinel**, and nothing else
  (`terminated_at`). It is a NODE name the six single-node benchmarks declare inside a
  `termnorm` pipeline. It once also named a connector; that connector was deleted, so
  the old two-meanings warning no longer applies.
- **thinking channel** — where a model puts its internal process: a `reasoning` slot in a
  node's `output_schema`, or the provider's native `message.reasoning` (captured as
  `LLMResponse.reasoning`). **Not optional instrumentation** — a model given nowhere to
  think answers without thinking. Captured to the ledger → audit twin →
  the node-detail "Thinking" pane, and **strictly analytical**: never a gate, metric,
  validator, scorer or cache key. Has no code reader by design; do not delete as dead
  surface (`docs/concepts/structured-output.md` § A place to think is part of the ask).
  Distinct from the backend's per-sample `reasoning_trace` (the TARGET model's thinking)
  and from `reasoning_chars` (a truncation diagnostic that counts and discards).
- **answer extraction** — a **double seam**, and only one arm is in this repo. The SHAPE
  arm is the BACKEND's (TermNorm `_step_llm_only`): it destructures the structured-output
  slot named by `answer_field` before the body is posted back. The LABEL arm
  (`scoring/formula/matchers.py`, `EXTRACTION_NOTES` + `SCORING_FUNCTIONS`) parses the
  answer prose and decides HIT/MISS. Display-side extraction is a third thing
  (`domain/rendering.py`). Shape first, label second.
- **`steps`** — `list[str]` as the reserved top-level `pipeline_params` key (active
  node names, `RESERVED_PIPELINE_PARAM_KEYS`); `list[dict]` on the backend's
  `GET /pipeline` payload (each `{"name": …}`). Walk the former with
  `node_config_items`, never a re-derived isinstance check.
- **`config`** — at least six referents: `CampaignConfig` (the campaign's knobs), a
  node's `nodes.{name}.config` overlay block, `config/settings.py` (install-global
  constants), `promptpotter/config/` (the package), a connector's
  `default_node_config`, and `node_config` (the wire key). Qualify the word.
- **`session`** — (1) a **campaign run's** session (`SessionStore`,
  `application/bootstrap/session.py`, `s_xxxx`); (2) a **browser login**
  (`OIDCSessionStore`, `infrastructure/identity/session.py`); (3) TermNorm's
  **backend handshake** (`POST /sessions` with a terms array, `TermNormSession`);
  (4) the `Session` wiring object threaded through the runner.
- **`index_terms`** — the retrieval index a `candidate_source` node ranks each query
  against; the second half of every connector's `extract_experiment -> (queries,
  index_terms)`. Empty for connectors with no retrieval index. Sourced from
  `candidate_library.txt` for an authored dataset.

## Loop layers — what generates, what refines, what replans

- **L1** — `l1_generate`: the candidate-mutation layer that emits new
  pipeline_params variants every round. Lives at
  `application/optimization/l1/` (`generate.py`, `score/`,
  `critique.py`, `execute.py`, `resume.py`, `population.py`,
  `stats.py`). Contract: `application/optimization/CLAUDE.md`.

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
- **Origin** — the **starting configuration** the potter loop evolves from,
  which is the same thing as **C0**, its first candidate: an individual *is*
  a configuration, and the origin resolves to an `OptSearchPoint`
  (`resolve_origin_opt_search_point`, `application/origin.py`) — the type
  every candidate is. For a fork, the point it branches *from*. It arrives
  **incomplete** (prompt fields + per-node pipeline config + the pipeline's
  required inputs + dataset binding); **check-in** completes and gates it,
  after which it is the parent of round 1's candidates. Full definition +
  the two gates: `docs/architecture.md` §0.5. The data a campaign starts
  from is a **Dataset**, never an "origin".
  - **origin's round-0 score** (`origin_accuracy` / round 0) — C0's
    measurement. Held solely as `rounds[0]`, in memory (`Cycle.origin_round`)
    and on disk alike; readers derive it via `origin_accuracy_of`
    (`campaign_store/store.py`). It has no sidecar on `Cycle`.
- **Parent** — the individual a candidate was mutated from, scored over the
  samples that candidate touched so every paired diff is matched
  (`RoundParent`, `domain/results.py`; built by `rescore_parent`, labelled
  with the parent individual's own label). **At round 0 the parent is the origin; after
  that it is the prior winner** — so "origin" is reserved for offset 0 and
  the fork point, and every other round says *parent*.
- **Campaign** — one declared optimization effort: a dataset, a
  pipeline origin, context text, and the optimizer meta-prompts it runs
  under. A first-class entity holding one session root + its fork/diag/
  sweep descendants. Directory `campaigns/{campaign_id}/` +
  `campaign.json` manifest. `campaign_id = {dataset}__{rand6_hex}` —
  minted fresh per `new` call by `mint_campaign_id`; each `new` produces
  a distinct campaign. The declaration (target hash +
  optimizer-prompt hash) is recorded as properties on `campaign.json`
  for drift detection on resume, not as the id. `domain/campaign.py`.
- **Session** — **not a persisted tier** (`docs/architecture.md` §0 "A
  campaign has one root cycle"). The surviving `Session`
  (`application/bootstrap/session.py`) is the in-process **wiring object**;
  `active_session.json` is the operator's pointer to the live campaign + cycle.
- **Unit** — one continuous-parameter run inside a campaign. A campaign
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
- **ForkSpec** — the single typed fork record (`domain/run_records.py`):
  one writer behind the parent's `FORK_CUT` ledger entry (SoT), the fork's
  `index.json::fork` (lineage read), and — when steered — its
  `CycleSeedRecord` (bootstrap read).
- **CycleSeed** — the chosen-searchpoint seed a non-root cycle begins from
  (`domain/run_records.py`); the seed prompt becomes the cycle's origin
  `OptSearchPoint` and its `pipeline_overlay` layers onto the dataset
  overlay for that cycle only. Merge contract:
  `promptpotter/application/CLAUDE.md` § Cycle-seed overlay. Non-operator
  triggers (sweep / diag / rebase) carry no seed.
- **operator_steered** — the `ForkTrigger` for an operator-initiated fork:
  pick a searchpoint, edit its prompt / node config / run limits, fork
  from it (a `CycleSeed` is required). Restarts round numbering at 1.
  Queryable in the lineage tree (`lineage.py`).
- **Cycle-seed I/O** — `CampaignStore.write_cycle_seed`/`read_cycle_seed`:
  the **read-once** `CycleSeedRecord` on the cycle's ledger, distinct from
  the **polled** `.runtime/{skip,pause,spend_cap}` flags; read at the
  single runner seam (`runner/entry.py::run_optimization`).
- **Sibling kind** — `root | fork | diag | sweep`. Recorded in the
  cycle's `index.json` metadata, not derived from a directory path.
  See `infrastructure/store/layout.py::sibling_kind`.
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
  Lives in the MeasurementArchive (`measurements/`).

## Scoring — facts vs policy

- **Hit** — boolean rank-1 exact match (per-sample classification).
- **Score** — continuous, formula-driven; feeds the optimizer.
  `application/scoring/formula/`.
- **Fitness / composite** — the scorer expression compiled from
  `campaign.json::scoring.per_sample` and `per_round`. Each measurement
  carries `{scorer_id: {score, hit, formula}}`. Rescored on every load.
  `application/scoring/search_point_scorer.py`.
- **metric / basis** — the cell any fitness number occupies: *metric* ∈ {accuracy,
  composite, ability θ} × *basis* ∈ {subset, matched, cumulative}. A cross-product,
  not redundancy — a candidate has a subset AND a matched AND a cumulative value at
  once. Name a number by its cell instead of re-explaining it.
- **`display_fitness`** — THE one composite-or-accuracy rule: active composite when
  present (honest `0.0` kept), else accuracy on `None`. Never add a second
  resolution. `domain/rendering.py`.
- **score_search_point()** — the single scoring gateway. Every scoring
  call MUST go through it. `application/scoring/search_point_scorer.py`.
- **Round scorer** — the optional `per_round` formula compiled from
  `campaign.json::scoring`. Recompiled on hot-swap. Lives on
  `session.scoring.round_scorer`.

## Elimination + ranking

- **PoBB** — Posterior of Being Best. Bayesian round-level elimination
  rule (knobs `optimization.pobb_epsilon` / `elimination_n_min`): paired
  sample-keyed priors, joint 1PL Rasch fit, closed-form `p_best` —
  [`concepts/paired-sample-pobb.md`](concepts/paired-sample-pobb.md).
  `application/optimization/pobb/checks.py`.
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
  `application/config.py::Estimand`.
- **knob** — one leaf of `CampaignConfig`, declaring on its own field (as
  `Annotated[..., Knob(scope, *estimands)]`) both what it shapes — `Scope.POLICY`
  (a decision knob; resume keeps the data trace) vs `Scope.DATA` (resume runs
  divergence detection) — and which estimand(s) it moves. `application/knobs.py::KNOBS`
  walks the model for them; it is the leaf taxonomy every other surface reads.
- **config coupling / config map** — the declared registry of which knob
  moves which estimand and which knobs *clash* (a combination that makes a
  shared estimand ill-defined or a tuned knob inert). One source of truth
  (`application/knobs.py`), read by the pre-run preflight warning,
  the `python -m promptpotter.diagnostics.config_map` table, and the webapp
  Config-map panel. Answers "what overwrites what" (provenance: effective
  value + source layer per knob) and "what clashes with what" (the active
  couplings). Where the "deferred-with-the-flip" interactions in
  [`specs/fitness-comparability.md`](specs/fitness-comparability.md) became
  machine-checked.
- **Shared round order** — the one deterministic scoring order every
  candidate in a round walks (`build_round_order`): seed-MISS
  win-opportunity samples first (asc δ), a seed-HIT regression probe
  every 4th slot (desc δ), unknowns riding the miss stratum. Pure
  function of (seed grades, δ ruler, ids) — resume re-derives it.
  `application/intelligence/adaptive_queue_mechanism.py`.
- **Paired-margin gate** — the PoBB futility gate: kill a candidate
  when `P(net ≥ margin wins vs the seed) < ε`, wins/losses counted on
  discordant pairs only, win rate estimated on the measured seed-MISS
  stratum (order-agnostic). `need > opportunities` ⇒ `binom_sf` = 0 =
  the deterministic can't-catch-up corner. Records as `margin_cut`.
  Folded the old dominance + equivalence gates.
  `application/optimization/pobb/checks.py::_margin_stats`.
- **pick-value** — the between-round CAT acquisition objective:
  `decision_information_gain + delta_learning_gain` (in nats). Drives
  `select_round_subset` ranking and the `pick_score` snapshot.
  Contract: [`specs/verdict-resolution.md`](specs/verdict-resolution.md).
- **Decision information gain** — the pick-value objective: the
  mutual information between the next outcome and
  the keep/abort verdict `θ_c > θ_s` against the seed. The
  means-known limit recovers Bernoulli Chernoff information.
- **pick_score snapshot** — descriptive per-sample
  `pick_score.per_sample` on the hard-samples artifact: the
  pick-value for a fresh mutation of the seed, ability
  prior `N(θ_seed, σ_θ²)` (centred on the parent, not the
  population-mean anchor 0). Consumed by the webapp dataset table.
  The artifact's `pick_score.sample_order` is `build_round_order`
  seeded by the best candidate — the order the engine will actually
  execute next round.
- **llm_ranking** — a backend node that orders ranked_items per
  sample. Distinct from PoBB, Rasch, and the shared round order.
- **prediction (terminal ranking)** — the per-sample `predicted` is the
  head of the **terminal ranker's** ranked output (`candidate_ranking`
  for a `token_matching`-terminal pipeline, `final_ranking` for an
  `llm_ranking`/`llm_only` one), derived by `terminal_ranking()` — the
  pipeline shape decides the source key, it is not hardcoded.
  `application/optimization/pobb/classification.py`.

## Escalation + healing

- **Patience** — L1's per-cycle stall budget. Bumps after each
  no-improvement round; resets on improvement.
- **Stall** — N consecutive non-improving rounds for L1, L2, or L3.
  Tracked on `EscalationFSM.l1_stall_count` etc.
- **Escalation** — the post-round router (`decide_escalation` over
  `DEFAULT_ESCALATION_RULES`) deciding CONTINUE / FIRE_L2 / STOP_*.
  `application/optimization/escalation/`.
- **Wound** — one of four self-healing paths, keyed by (detection point ×
  **nurse owner**), never by producer: Wound 1 (L1 validation failure → L1
  re-proposes a valid override), Wound 2 (mid-eval degradation → L1 retunes
  the node config, or OPERATOR on a fatal), Wound 3 (L2 stall → L3 replans),
  Wound 4 (L2 guard-breach → L3 replans). Storage is four typed lists;
  rendering collapses to two owner-grouped signals — `l1_wounds`
  (validation + runtime) and `guard_breaches` (L2 + L3 post-parse). See
  `docs/developer/self-healing-internals.md`.
- **Guard breach** — programmatic post-parse validator outcome on the
  L2 or L3 LLM output. Fields:
  `OptSearchPoint.wounds.l2_guard_breaches`, `wounds.l3_guard_breaches`.

## Dispatch — info-flow to optimizer prompts

- **INJECTIONS** — the typed registry of every `{{slot}}` an optimizer
  prompt can reference. `application/optimization/dispatch/injections/registry.py`.
- **DispatchHub** — the facade with one `fill` path: fills a node's layout
  (`NODE_LAYOUTS[node].floor`, or L2's live `l1_layout` for `l1_generate`) +
  resolves non-layout `{{token}}`s → `(filled_template, injection_vars)`.
  `application/optimization/dispatch/facade.py`.
- **Bundle** (`InjectionBundle`) — frozen per-call state container
  every renderer reads. Built by `build_bundle(cycle)`.
  `application/optimization/dispatch/bundle.py`.
- **l1_layout** — the L2-authored placeholder layout that drives which
  signals appear in each L1 prompt slot. Read at L1 fill time.
  `domain/l1_layout.py`.
- **Evidence grounding** — every L1 variant declares an
  `evidence_grounding: {field, citation}` naming the panel entry that
  justifies the mutation. The citable panels are derived per round from
  the node's live layout (`citable_fields`, `dispatch/injections/
  registry.py`), so a panel L1 was not shown cannot be cited. Validated by
  `application/optimization/validators/l1_behavior.py`.
- **Meta-prompt** — synonym for "optimizer prompt" (L1/L2/L3/Critique
  LLM template). Field-standard from PromptWizard / DSPy / OPRO.
- **Prompt homes** — three, don't confuse them. The **target** prompt the optimizer
  evolves: `datasets/{name}/prompts/{node}.json`. The **optimizer's** meta-prompts
  (install-global): `datasets/_optimizer/pipeline.json::resolved_prompts` — keyed
  `{node}/{n}`, so check-in's second mode lives at `resolved_prompts.checkin/2`. The
  **outer** L4 meta-prompts: `datasets/_optimizer_meta/prompts.json` (prompt fields only,
  deliberately no `pipeline.json`; selected per-cycle by `OptimizationConfig.optimizer_set`).
  A per-node **overlay** (`pipeline.json::nodes.{name}.config.prompt`) is a fourth, and is
  a tunable, not a home.
- **L4** — PromptPotter optimizing its own meta-prompts: an outer cycle whose backend
  is an inner cycle. **Recursion, not a fourth layer** — the ladder is closed at
  L1/L2/L3 and there is no `l4_*.py`. Lives at the connector seam
  (`connectors/promptpotter.py`) + `runner/inner/cycle.py::run_inner_cycle`, driven
  by `datasets/promptpotter-self/`. Plan: `docs/specs/l4-outer-loop.md`.
- **sweep** — a cheap A/B of L1 candidates ahead of full promotion: sibling cycles
  under `campaigns/{id}/sweeps/{batch_id}`, run by `python -m promptpotter new --sweep-batch`.
  `application/sweep/`. A sweep cycle carries no `CycleSeed`; `sibling_kind == "sweep"`.
- **Second prompt** — a structured-output schema viewed as input; its three
  levers are names, order, `description=`. `docs/concepts/structured-output.md`.
- **Shape-determinism** — a schema guarantees a parseable object with the fields
  you named, never the content in them. `docs/concepts/structured-output.md`.
- **checkin** — the fifth optimizer node. One node, two
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
  name in `CONNECTORS`. Two today: `termnorm` and `promptpotter`
  (L4). `DEFAULT_CONNECTOR` names the fresh-upload default.
  `promptpotter/connectors/`.
- **Backend** — a connector's running service (TermNorm's FastAPI is
  the canonical example). Read-only from PromptPotter's perspective —
  we never edit a backend's static config.
- **Pipeline** — the connector's typed node graph self-described via
  `GET /pipeline`. Built into `PipelineSchema` at boot.
  `domain/pipeline_schema.py`.
- **Node** — one step in the pipeline. Has runtime, node_type, and
  `optimizer.param_keys`. NEVER "service" or "building block."
- **Overlay** — per-dataset operator delta merged onto each wire
  payload. Lives at `datasets/{name}/pipeline.json::nodes.{name}.config`.
  The sole route for changing a backend tunable.
- **pipeline_params** — node-keyed config dicts plus the reserved `steps`
  list of active node names (`{"steps": [...], "llm_only": {"model": …}}`).
  The canonical optimizer-layer shape; never a flat `{param: value}` map.

## Persistence — what writes where

- **CycleEventLog** — the single persistence ingress per cycle. Owns
  `.runtime/ledger.jsonl`. (`events.jsonl` is the *workspace* ledger at
  `.workspace/events.jsonl` — different scope, different file.)
  `infrastructure/ledger.py`.
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
