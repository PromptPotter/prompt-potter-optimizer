# domain/ — frozen models + pure types

The pure layer: the frozen models and types every other layer passes
around. No I/O, no async, no infrastructure imports — anything needing a
`BackendClient` or `Stores` belongs one layer up, in
[`../application/CLAUDE.md`](../application/CLAUDE.md)'s tree.

## Backbone primitives

| Primitive | File | Why it's settled |
|---|---|---|
| `JobSearchPoint` | `search_point.py` | Frozen target spec. Its cells are filed by its node chain (`measurement_archive.py::config_key`); `content_hash(dataset)` names a campaign's root only. First positional arg to `score_search_point()` (`application/scoring/search_point_scorer.py`). |
| `PromptTemplate` | `opt_search_point.py` | Prompt scheme — the `PROMPT_STRING_FIELDS` decomposition fields — with `render()` / `compile_prompt()`. **The constant is the field SET; the render ORDER is per class** (`RENDER_ORDER`, permutation-checked at import) — the base orders for the archive key, so any target prompt (an `OptSearchPoint`, an export's `template()`) renders as scored; `OptimizerPromptTemplate` alone orders for cache prefixes, and re-coupling them re-cuts every banked cell. Canonical prompts at `datasets/{name}/prompts/{node}.yaml`. |
| `OptSearchPoint` | `opt_search_point.py` | The individual: the `PROMPT_STRING_FIELDS` decomposition fields + `shot_ids` + `pipeline_params` (its resolved node config, written by `configured` alone) + `lineage` (`parent_ids`, `variations`). **`loci()` is the one roster of its parts, and a child is made by `derive` / `edited` alone**, so each node's variation records the loci it wrote. **Its `id` is that content, never stored**, so the same configuration reached twice is one individual and a resume names the ids its ledger holds; a REJECTED proposal measured no individual and is named by its slot (`scoring/candidate_report.py::arm_id`). **No optimizer state rides it** — that is per-optimizer typed state (`optimizer_state.py`: the `{manifest, population, prompt_hashes, payload}` envelope around a registered `RoundPayload`), banked on every round's close and restored from there on resume and fork. **Neither the campaign's framing nor its shots' content is on it**: `target_fields(framing, demo=)` splices the one and resolves the other's demo-pool ids at render, so every render of a scored prompt is handed the pool. |
| `CheckpointKind` | `run_records.py` | The base every decision-kind enum subclasses — the bench's `BenchCheckpointKind` here, each optimizer's in its own package — so a decision's kind names the party that took it; a ledger record carries its value. Whether a resume re-derives a kind is whether a replayer is registered for it (`application/bench/resume_and_fork/replayers.py::replayers`, the SoT for replayed-vs-archival) — no second table. |
| `ForkSpec` / `CycleSeed` / `ConfigOverrides` | `run_records.py` | The one typed fork record + the chosen starting point a non-root cycle begins from (`{origin_prompt_fields, pipeline_overlay, config_overrides, origin_source}`). `ConfigOverrides` is the fork's whole campaign-config delta; `scoring` sits on `CampaignConfig` itself rather than under `optimization`, so it needs its own bucket at the apply seam. Each is a `Knob` whose scope says it must FORK rather than mutate the running cycle. **An operator fork is one of two acts, and `keep_rounds` is which**: unset, `operator_steered` — a clean offshoot from the origin; set, `operator_rewind` — rounds `0..N-1` lifted, the branch continuing at N under the overrides, which is what applying a scoring mask means. Both carry a `CycleSeed` (the `fork-cycle` payload's `seed` is one on the wire, less the server-stamped `origin_source`); the rewind refuses one declaring `origin_prompt_fields`, since the lifted round 0 already is its origin; the mint seam writes one for campaign-from-origin; an L2/L3 `fork_proposal` carrying an unlock writes one too (config delta, no origin — `origin_source` empty, since a rebase replays its own C0); a diag carries no seed. `origin_source` (`fork_seed` \| `campaign_origin`) names C0's `changes_description`; its `source` stays the bench's `origin`. For forks: one writer (`mint_fork`, `application/bench/resume_and_fork/fork_siblings.py`), three records — the `FORK_CUT` decision on the parent, the fork's own `CycleMintedRecord` carrying the `ForkSpec` (which `CycleIndex.fork` is folded from), and the read-once `CycleSeedRecord` (the chosen starting point, appended by `write_cycle_seed`, `infrastructure/store/campaign_store/store.py`). |
| `PipelineSchema` / `PipelineNode` | `pipeline_schema.py` | Built from `GET /pipeline` (pure parser in `pipeline_parsing.py`); no BACKEND constants — the structured-output axis vocabulary (`SCHEMA_TOGGLE_PARAM`, `SCHEMA_DESCRIPTION_PREFIX`, `OUTPUT_CONTRACT_KEYS`) is ours, named once here so the parser that injects it, the fold that spends it and the served row all spell it the same. One field is not the backend's: `model_capabilities`, attached where a workspace is in hand, so `param_options` can answer a value space without this layer doing I/O. It rides the schema and never the identity — `node_configs`/`sp_hash` fold node configs, so a refreshed snapshot re-keys no banked measurement. |
| `RoundResult` | `results.py` | Per-round outcome, including `deprecated` (sanctioned vocabulary for fatal-warning sample lifecycle) and `ability` (below). **It is `RoundOutcome` plus its rows**: `RoundOutcome` is what `RoundClosedRecord` carries onto the ledger — with each row as its archive address (`RoundCells`) — so a field declared on it is ledger shape, and a rename there erases every close that carried the old name (§ Tolerance, the Ledger case). **The envelope is optimizer-neutral**: each arm carries its reading against what it was measured against (`vs_reference`, rows in `reference_results`), the election is `selected_labels` (empty = held) plus `leading_label` (the one arm the round is read off, which the selector names and no reader re-ranks), and every optimizer-specific readout rides `optimizer_state`. Its `scoreboard` is persisted IN RANK ORDER, so `scoreboard_rank_key` — the key deciding that order — and `order_floor` under it live beside the model, never with the renderers in `application/views/`. |
| `AbilityReading` | `ruler.py` | **A θ, the δ scale it was read on, and whether that scale makes it ability — ONE value.** **No field defaulted**, so no producer can stamp a level and leave its scale or its caveat to be inferred. `comparable_to` is the only sanctioned test for whether two θ may be differenced; `scale()` is the single rendering; `caveat` is the served `ThetaCaveat`, decided once by `ruler.py::theta_caveat` for screen and optimizer alike. Not a `@computed_field`: this model is `extra="forbid"`, so a derived key would serialize and then refuse to read back. |
| `DeltaRuler` | `ruler.py` | **The δ scale every θ in the system is read on.** **The anchor is stamped at LOCK and never moves**: the ruler GROWS by anchored extension (`intelligence/exploration.py::extend_ruler`) while shared δ stay bit-identical, so `ruler_id` names the anchor, never the membership (`ruler.py::anchor_id_of`). `entries_covering` exists for mid-round PoBB alone; everywhere else a cell a warm ruler does not carry enters no θ and is served as `ThetaCaveat.UNMEASURED_DELTA` (`unlinked`). |

## Sanctioned `deprecated` vocabulary

**Write `deprecated` only as domain language for the fatal-warning sample
lifecycle** — `Sample.is_deprecated`, `deprecated_samples` lists,
`RoundResult.deprecated`, `retry_of_deprecated_cache`. Those four are the
whole set: a sample's state, never a back-compat shim (root `CLAUDE.md` § STOP).
The word `legacy` is **never** sanctioned.

## Other surfaces

- `l4/` — **the L4 law, and its only home.** `proxies.py`: what ONE finished inner cycle says
  about the optimizer prompt that ran it — the floor / exclude / measure trichotomy, plus
  `OuterSampleProxies`, whose single field may not be defaulted. Which reading that field takes,
  and every term the panel retired, is argued in
  [`../../docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md) § The measurand.
  **The estimator digest hashes `proxies`, so only what decides a cell's number lives there** —
  an identity reader (`inner_origin.py`) sits beside it. `__init__.py` re-exports nothing — import
  the module, never the package.
- `export.py` — the export artifact (`cycles/{id}/export.json`): the winning prompt by field name
  plus the provenance that makes its fitness readable — the formula the number was computed under,
  n, lift + CI, θ, the rows' own hash, the optimizer manifest — and an `artifact_version` a reader
  refuses on. Pure over ONE `RoundResult`, which is what keeps it here: the projection can grow no
  file read and no session dependency. **The round it projects is the optimizer's declared pick,
  origin round included** — a campaign that never selected past it exports its origin under round
  0, not nothing. Read the round file's `prompt_fields`, never `CycleResult.result_prompt_fields`:
  that one is the wire-side projection. The shots ride resolved, as `few_shot_block`, because a
  reader outside the campaign has no demo pool to resolve an id against.
- `spend.py` — tokens and money, at both arities: `TokenAccount` is ONE call's (or one row's)
  consumption, `SpendBucket` / `SpendRollup` the same account summed over a cycle. `results.py`
  imports `SpendRollup` for `CycleResult.spend` and deliberately does not re-export it. What the
  buckets MEAN is stated on the fields.
  **`TokenAccount` IS the carrier, not a converter** — every client returns one on
  `LLMResponse.usage`, `StepUsage` beside it is its wire spelling, and the emit seam is the
  one place it flattens onto `TokenUsageRecord` — never hand-convert it at a call site.
  Its `cache_share(*, replayed)` is likewise the only reading of a provider's prefix-cache
  discount — the kwarg is required so no renderer can omit the arm on which the number is a lie —
  and `prefix_reading` beside it the only rendering of it: the reading is
  TOTAL over four states, so no surface may suppress it on `> 0`. It is SERVED whole
  (`PrefixReading` on a spend kind, a candidate row and a node block), so the browser divides no
  counts.
- `activity.py` — what a run is doing NOW: `ActivityFeed` reads each typed `CycleRecord` into at
  most one `ActivityItem` and folds them into `ActivityState`. **The one reader of a record as a
  line** — the SSE tail and the time-ray both serve it, so a wording, a lifetime (`LIFETIME`) or
  a new record kind changes here and nowhere else.
- `optimizer_state.py` — an optimizer's own working state: the `OptimizerState` envelope
  (`{manifest, population, prompt_hashes, payload}`) every round's close banks — `population` the
  bench's, the individuals the run carries — and `RoundPayload`, the base
  each optimizer's payload subclasses IN ITS OWN PACKAGE, registered under its manifest's name as
  `application/` loads; an unregistered one raises past every tolerant read. The bench restores it on
  resume and fork and reads nothing inside `payload`; a reader narrows through `payload_as`.
- `campaign.py` — `Campaign`, the frozen manifest (`campaign.json`) and single owner of the
  frozen `CampaignConfig` snapshot.
- `cycle_paths.py` — how a cycle is ADDRESSED. `CycleHop` (the `(campaign, cycle)`
  pair) and its root→leaf chain `CyclePath` are the address type for the campaign
  store *and* the served tree: a cycle_id is content-addressed on the origin and
  repeats across sibling `.inner` sandboxes, so neither half names an entity alone
  and a pair of loose `str`s can be passed swapped with every gate green. **An object
  holding both exposes it as a property** — `Campaign.root_hop`, `Session.hop`,
  `SessionCtx.hop`, `Job.hop` — because re-pairing them at a call site is a second
  spelling of a fact that object owns. Plus the `CycleDir` / `WorkspaceDir`
  write-target newtypes (passed through, never reconstructed from `str`);
  `dashboard.json` is per-cycle, so projections bind to `CycleDir`.
- `pipeline_overlay.py` — the SHAPE of a `pipeline_params` dict: `RESERVED_PIPELINE_PARAM_KEYS` +
  `node_config_items` (the canonical walk), the two overlay predicates and `fold_output_contract`.
  A re-derived `k == "steps" and isinstance(…)` is a second definition of a node config.
- `wire_record.py` — a slots dataclass as its own wire record: `null` reads as absent, a
  wrong-typed value raises `TypeError`, and what the reader builds is immutable one level down.
- `candidate_diff.py` — what a candidate CHANGED (`candidate_delta`, `parent_param_value`) and whether that change is an idea already tried (`idea_fingerprint`,
  `same_idea`, `candidate_idea`, the `IDEA_*` thresholds), plus the render side (`flatten_sp_summary`,
  `build_candidate_flat`, `group_diff_keys`). **Both questions live in one module on purpose:** all
  three consumers of "already tried" — round-local dedup, the cross-round repeat gate, the ALREADY
  TRIED panel — must share both definitions, or a re-proposal one rejects is rendered as new by another.
- `pipeline_parsing.py` — pure dict → `PipelineSchema` parser.
- `validators.py`, `phases.py`, `wounds.py`, `round_diagnostics.py`,
  `scoring.py`, `connector.py`, `backend.py`, `sample.py`, `bench.py` —
  domain types and pure logic shared across the application layer.

## Inherit `StrictModel`, never `BaseModel`

**Every model here inherits `StrictModel` (`strict_model.py`).** Pydantic's default is
`extra="ignore"`, so an unknown key is dropped and a misspelled kwarg is a silent no-op.
`model_config` merges across
inheritance, so a subclass adds `frozen=True` without restating `extra`. A model that
must stay lax says so on itself and states why; the ledger's `models_lax` counts them.

## Tolerance is scoped by what a payload is FOR

A round is read back off its `RoundClosedRecord`, whose `extra="forbid"` reaches every model
nested inside it. A renamed field is therefore fatal in one direction or the other, and which
outcome is CORRECT depends on what the payload carries:

- **Reporting** — `round_diagnostics.py`'s rows. Nothing gates, scores or escalates on them, so
  every field defaults: a lost name degrades instead of killing a paid measurement.
- **Scoring** — `ScoredCandidate`, `StopSignal`, `OptSearchPoint` and its subtree. These
  stay required. A missing field means the record cannot be vouched for, and a tolerant read
  hands back a winner prompt with a silently defaulted field — a wrong answer beats an
  unreadable one only until someone believes it.
- **Ledger** — the `CycleRecord` arms. Tolerant by SKIP, not by default: `ledger.py::iter` logs
  a key no arm declares and drops the WHOLE line, so a field DELETE erases every record carrying
  it and nothing raises. Here pruning IS the repair — `restamp.py::_prune_record`, derived from
  the union, so no field delete needs a migration of its own.

`application/maintenance/restamp.py::check_round_closes` reports a close that no longer loads —
a round the scan silently stops counting; PRUNING never repairs one, because it cannot restore a
renamed field's value.

## Conventions

- Frozen models by default; lineage via `derive()`, never mutation.
- `mypy` `strict = true` is **global** (`pyproject.toml`), not a per-layer tier: the only
  overrides are `tests.*` (loose by charter) and a third-party `follow_imports = "skip"`
  list. `domain/` is not an `Any`-free zone either — the ledger's `any_params` tracks that debt.
