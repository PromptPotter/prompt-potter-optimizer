# domain/ — frozen models + pure types

The pure layer: the frozen models and types every other layer passes
around. No I/O, no async, no infrastructure imports — anything needing a
`BackendClient` or `Stores` belongs one layer up, in
[`../application/CLAUDE.md`](../application/CLAUDE.md)'s tree.

## Backbone primitives

| Primitive | File | Why it's settled |
|---|---|---|
| `JobSearchPoint` | `search_point.py` | Frozen target spec, content-hashed via `content_hash(dataset)` (`shared/hashing.py` — not this layer). First positional arg to `score_search_point()` (`application/scoring/search_point_scorer.py`). |
| `PromptTemplate` | `opt_search_point.py` | Prompt scheme — the `PROMPT_STRING_FIELDS` decomposition fields plus `few_shot_examples` and `plan` — with `render()` / `compile_prompt()`. **The constant is the field SET; the render ORDER is per class** (`RENDER_ORDER`, permutation-checked at import) — the optimizer prompt orders for cache prefixes, the target prompt for the archive key, and re-coupling them re-cuts every banked cell. **Two denominators are in use and they mean different things:** the *decomposition* fields are what L1 mutates; the *template* is that set plus the two above. Say which; never a bare count. Canonical prompts at `datasets/{name}/prompts/{node}.yaml`. |
| `OptSearchPoint` | `opt_search_point.py` | Optimizer state: the `PROMPT_STRING_FIELDS` decomposition fields + `few_shot_examples` + `plan` + `lineage` + `memory: L2L3Memory` (wounds / l1_layout / l1_overrides / task_context). **All new optimizer state flows through here** — no sidecar state. |
| `ResumeCheckpointKind` | `run_records.py` | The enum. Its gating table `RESUME_CHECKPOINT_GATING` lives one layer up in `application/optimization/resume_and_fork/decisions.py` (it is the SoT for replayed-vs-archival) — import-time exhaustiveness there raises if a kind has no gating mode. |
| `ForkSpec` / `CycleSeed` / `ConfigOverrides` | `run_records.py` | The one typed fork record + the chosen starting point a non-root cycle begins from (`{origin_prompt_fields, pipeline_overlay, config_overrides, origin_source}`). `ConfigOverrides` is the fork's whole campaign-config delta — run limits, the two policy toggles (`per_round_resubset`, `schema_field_rename`), and `scoring`, which sits on `CampaignConfig` itself rather than under `optimization` and so needs its own bucket at the apply seam. Each is a `Knob` whose scope says it must FORK rather than mutate the running cycle. **An operator fork is one of two acts, and `keep_rounds` is which**: unset, `operator_steered` — a clean offshoot from the origin; set, `operator_rewind` — rounds `0..N-1` lifted, the branch continuing at N under the overrides, which is what applying a scoring mask means. Both carry a `CycleSeed` (the wire `OperatorForkOverride` command payload deserializes into it); the rewind refuses one declaring `origin_prompt_fields`, since the lifted round 0 already is its origin; the mint seam writes one for campaign-from-origin; an L2/L3 `fork_proposal` carrying an unlock writes one too (config delta, no origin — `origin_source` empty, since a rebase replays its own C0); sweep + diag carry no seed. `origin_source` (`fork_seed` \| `campaign_origin`) stamps the C0 lineage. For forks: one writer (`_mint_fork`, `application/optimization/resume_and_fork/fork_siblings.py`), projections on the ledger + index — the `FORK_CUT` record (lineage SoT), the read-once `CycleSeedRecord` (the chosen starting point, appended by `write_cycle_seed`, `infrastructure/store/campaign_store/store.py`), and `index.json::fork` (lineage-read copy). |
| `PipelineSchema` / `PipelineNode` | `pipeline_schema.py` | Built entirely from `GET /pipeline` (pure parser in `pipeline_parsing.py`); zero backend constants. |
| `RoundResult` | `results.py` | Per-round outcome, including `deprecated` (sanctioned vocabulary for fatal-warning sample lifecycle) and `ability` (below). |
| `AbilityReading` | `ruler.py` | **A θ, the δ scale it was read on, and whether that scale makes it ability — ONE value.** `theta` / `se` / `ruler_id` / `ruler_n` / `ruler_span` / `round_span` / `calibration_model` / `caveat`, **none defaulted**, so no producer can stamp a level and leave its scale or its caveat to be inferred. `comparable_to` is the only sanctioned test for whether two θ may be differenced (same `ruler_id`, never where either is `None` — that names NO scale); `scale()` is the single rendering. `caveat` is the served `ThetaCaveat` — cold ruler, flat ruler or collapsed band — decided once by `ruler.py::theta_caveat`, which the `confounds` panel calls too, so screen and optimizer cannot disagree about whether a number means anything. Not a `@computed_field`: this model is `extra="forbid"`, so a derived key would serialize and then refuse to read back. |
| `DeltaRuler` | `ruler.py` | **The δ scale every θ in the system is read on** — per-sample difficulty + SE, the 2PL discriminations, the `mu_delta`/`sigma_delta`/`sigma_theta` the fit converged to, and `anchor_id`. It lives in its own module because `results.py` and `run_records.py` both need it and the first already imports the second, so neither could host it. **The anchor is stamped at LOCK and never moves**: the ruler GROWS by anchored extension (`intelligence/exploration.py::extend_ruler`) while shared δ stay bit-identical, which is what makes θ on a 20-cell subset comparable to θ on the 70-cell ruler it came from — so `ruler_id` names the anchor, never the membership, and hashing the membership would declare a cycle incomparable with itself. `entries_covering` completes a miss at `mu_delta` and exists for mid-round PoBB alone; everywhere else a warm ruler that does not carry a scored cell RAISES (`RulerCoverageError`), because grading it `δ=0.0` reads an off-ruler cell as easier than anything ever measured. |

## Sanctioned `deprecated` vocabulary

**Write `deprecated` only as domain language for the fatal-warning sample
lifecycle** — `Sample.is_deprecated`, `deprecated_samples` lists,
`RoundResult.deprecated`, `retry_of_deprecated_cache`. Those four are the
whole sanctioned set; they name a sample's state, never a back-compat shim
(root `CLAUDE.md` § STOP bans those outright). The word `legacy` is
**never** sanctioned.

## Other surfaces

- `l4/` — **the L4 law, and its only home.** `proxies.py`: what ONE finished inner cycle says
  about the optimizer prompt that ran it — the floor / exclude / measure trichotomy, plus
  `OuterSampleProxies`, whose single field may not be defaulted. Which reading that field takes,
  and every term the panel retired, is argued in
  [`../../docs/specs/l4-outer-loop.md`](../../docs/specs/l4-outer-loop.md) § The measurand.
  `__init__.py` re-exports nothing — import `l4.proxies`, never the package.
- `export.py` — the export artifact (`cycles/{id}/export.json`): the winning prompt by field name
  plus the provenance that makes its fitness readable — the formula the number was computed under,
  n, lift + CI, θ, the rows' own hash, the optimizer manifest — and an `artifact_version` a reader
  refuses on. Pure over ONE `RoundResult`, which is what keeps it here: the projection can grow no
  file read and no session dependency. **The round it projects is the one the composite high-water
  names, origin round included** — a campaign nothing beat exports its origin under round 0, not
  nothing. Read the round document's `prompt_fields`, never `CycleResult.winner_prompt_fields`:
  that one is the wire-side projection and has already flattened `few_shot_examples` into a
  rendered block that `from_prompt_fields` cannot restore.
- `spend.py` — `SpendBucket` / `SpendRollup`: a cycle's money, and the only concern in this layer
  that is not about rounds, candidates or verdicts. Apart from `results.py` so a program reusing
  the engine can take money-counting as a file rather than carve it out of a section; `results.py`
  imports `SpendRollup` for `CycleResult.spend` and deliberately does not re-export it. What the
  buckets MEAN — bill vs incurred, why `reasoning_tokens` is a subset added into no total, why an
  unpriced count makes `total_used_usd` a floor — is stated on the fields themselves.
- `campaign.py` — `Campaign` frozen manifest (`campaign.json`); the
  first-class optimization-effort entity, single owner of the frozen
  `CampaignConfig` snapshot.
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
  `node_config_items` (the canonical walk over the tunable surface), the two overlay predicates,
  and `fold_schema_descriptions`. Read this instead of re-deriving `k == "steps" and isinstance(…)`
  at a call site — that re-derivation is how two sites came to disagree about what a node config is.
- `candidate_diff.py` — what a candidate CHANGED (`candidate_delta`, `parent_param_value`,
  `variant_prose_written`) and whether that change is an idea already tried (`idea_fingerprint`,
  `same_idea`, `candidate_idea`, the `IDEA_*` thresholds), plus the render side (`flatten_sp_summary`,
  `build_candidate_flat`, `group_diff_keys`). **Both questions live in one module on purpose:** all
  three consumers of "already tried" — round-local dedup, the cross-round repeat gate, the ALREADY
  TRIED panel — must share both definitions, or a re-proposal one rejects is rendered as new by another.
- `pipeline_parsing.py` — pure dict → `PipelineSchema` parser.
- `validators.py`, `phases.py`, `escalation_signals.py`, `round_diagnostics.py`,
  `scoring.py`, `connector.py`, `backend.py`, `sample.py`, `l1_layout.py` —
  domain types and pure logic shared across the application layer.

## Inherit `StrictModel`, never `BaseModel`

**Every model here inherits `StrictModel` (`strict_model.py`).** Pydantic's default is
`extra="ignore"`, so an unknown key is dropped and a misspelled kwarg is a silent no-op
— that is how `ObservationMapping(obs_key=…)` (the field is `output_field`) rode a real
`pipeline.yaml` for months with every gate green. `model_config` merges across
inheritance, so a subclass adds `frozen=True` without restating `extra`. A model that
must stay lax says so on itself and states why; the ledger's `models_lax` counts them.

## Tolerance is scoped by what a payload is FOR

The round document is read back off disk, and `RoundResult`'s `extra="ignore"` forgives an
extra key but not a missing one — nor does it reach the `extra="forbid"` models nested inside
it. A renamed field is therefore fatal in one direction or the other, and which outcome is
CORRECT depends on what the payload carries:

- **Reporting** — `round_diagnostics.py`'s rows. Nothing gates, scores or escalates on them, so
  every field defaults: a lost name degrades instead of killing a paid measurement.
- **Scoring** — `ScoredCandidate`, `EscalationSignal`, `OptSearchPoint` and its subtree. These
  stay required. A missing field means the record cannot be vouched for, and a tolerant read
  hands back a winner prompt with a silently defaulted field — a wrong answer beats an
  unreadable one only until someone believes it.
- **Ledger** — the `CycleRecord` arms. Tolerant by SKIP, not by default: `ledger.py::iter` logs
  a key no arm declares and drops the WHOLE line, so a field DELETE erases every record carrying
  it and nothing raises. Here pruning IS the repair — `restamp.py::_prune_record`, derived from
  the union, so no field delete needs a migration of its own.

`application/restamp.py::check_round_documents` reports which side has drifted; PRUNING never
repairs a round document, because it cannot restore a renamed field's value. Recovering one from a
record that survives is a different act and may write (`restamp.py::backfill_inner_facts`).

## Conventions

- Frozen models by default; lineage via `derive()`, never mutation.
- `mypy` `strict = true` is **global** (`pyproject.toml`), not a per-layer tier: the only
  overrides are `tests.*` (loose by charter) and a third-party `follow_imports = "skip"`
  list. `domain/` is not an `Any`-free zone either — it still carries bare `Any` params,
  counted by the ledger's `any_params`, which is where that debt is tracked down.
