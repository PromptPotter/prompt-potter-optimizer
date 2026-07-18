# domain/ — frozen models + pure types

The pure layer. No I/O, no async, no infrastructure imports. Models are
frozen Pydantic; mutation isn't a thing the type permits. Lineage is
encoded by `derive()`.

## Backbone primitives

| Primitive | File | Why it's settled |
|---|---|---|
| `JobSearchPoint` | `search_point.py` | Frozen target spec, content-hashed via `content_hash(dataset)`. First positional arg to `score_search_point()`. |
| `PromptTemplate` | `opt_search_point.py` | Prompt scheme — six `render()` decomposition fields (`PROMPT_STRING_FIELDS`) + `few_shot_examples` + `plan` — with `render()` / `compile_prompt()`. Canonical prompts at `datasets/{name}/prompts/{node}.json`. |
| `OptSearchPoint` | `opt_search_point.py` | Optimizer state: the 6 decomposition fields + `few_shot_examples` + `plan` + `lineage` + `memory: L2L3Memory` (wounds / l1_layout / l1_overrides / task_context). **All new optimizer state flows through here** — no sidecar state. |
| `ResumeCheckpointKind` | `run_records.py` | The enum. Its gating table `RESUME_CHECKPOINT_GATING` lives one layer up in `application/optimization/resume_and_fork/decisions.py` (it is the SoT for replayed-vs-archival) — import-time exhaustiveness there raises if a kind has no gating mode. |
| `ForkSpec` / `CycleSeed` / `ConfigOverrides` | `run_records.py` | The one typed fork record + the chosen starting point a non-root cycle begins from (`{origin_prompt_fields, pipeline_overlay, config_overrides, origin_source}`). `ConfigOverrides` is the fork's whole `OptimizationConfig` delta — run limits + two policy toggles (`per_round_resubset`, `schema_field_rename`), each bound to `Estimand.SEARCH`, so changing one MUST fork rather than mutate the running cycle. Every operator fork is `operator_steered` and carries a `CycleSeed` (the wire `OperatorForkOverride` command payload deserializes into it); the mint seam writes one for campaign-from-origin; an L2/L3 `fork_proposal` carrying an unlock writes one too (config delta, no origin — `origin_source` empty, since a rebase replays its own C0); sweep + diag carry no seed. `origin_source` (`fork_seed` \| `campaign_origin`) stamps the C0 lineage. For forks: one writer (`_mint_fork`), projections on the ledger + index — the `FORK_CUT` record (lineage SoT), the read-once `CycleSeedRecord` (the chosen starting point, appended by `write_cycle_seed`), and `index.json::fork` (lineage-read copy). |
| `PipelineSchema` / `PipelineNode` | `pipeline_schema.py` | Built entirely from `GET /pipeline` (pure parser in `pipeline_parsing.py`); zero backend constants. |
| `RoundResult` | `results.py` | Per-round outcome, including `deprecated` (sanctioned vocabulary for fatal-warning sample lifecycle). |

## Sanctioned `deprecated` vocabulary

The only sanctioned uses of `deprecated` in the codebase are domain-language:

- `Sample.is_deprecated`, `deprecated_samples` lists
- `RoundResult.deprecated`
- `retry_of_deprecated_cache`

These are core domain language for the fatal-warning sample lifecycle, not
back-compat shims. The word `legacy` is **never** sanctioned.

## Other surfaces

- `l4/` — **the L4 law, and its only home.** `proxies.py`: what ONE finished inner cycle says
  about the meta-prompt that ran it — the floor / exclude / measure trichotomy, plus
  `OuterSampleProxies` (the type IS the law; nothing restates it). `verdict.py`: what a ROUND of
  them says about a variant. It lives in `domain/` because it is pure over `CycleResult` — that
  is what stops it growing a file read or a session dep, which is exactly how it drifted before.
  Import the submodule, never the package: `domain.results` imports `verdict` while `proxies`
  imports `domain.results`, so a re-exporting `__init__` would make two acyclic modules circular.
- `campaign.py` — `Campaign` frozen manifest (`campaign.json`); the
  first-class optimization-effort entity, single owner of the frozen
  `CampaignConfig` snapshot.
- `cycle_paths.py` — `CycleDir`, `WorkspaceDir` newtypes (used by stores +
  projections; passed through, never reconstructed from `str`). `dashboard.json`
  is per-cycle — projections bind to `CycleDir`.
- `pipeline_parsing.py` — pure dict → `PipelineSchema` parser.
- `validators.py`, `phases.py`, `escalation_signals.py`, `round_diagnostics.py`,
  `scoring.py`, `connector.py`, `backend.py`, `sample.py`, `l1_layout.py` —
  domain types and pure logic shared across the application layer.

## Conventions

- PEP 604 type hints (`X | None`, `list[str]`).
- `mypy` `strict = true` is **global** (`pyproject.toml`), not a per-layer tier: the only
  overrides are `tests.*` (loose by charter) and a third-party `follow_imports = "skip"`
  list. `domain/` is not an `Any`-free zone either — it still carries bare `Any` params,
  counted by the ledger's `any_params`, which is where that debt is tracked down.
- **Inherit `StrictModel` (`strict_model.py`), not `BaseModel`.** Pydantic's default is
  `extra="ignore"`, so an unknown key is dropped and a misspelled kwarg is a silent no-op
  — that is how `ObservationMapping(obs_key=…)` (the field is `output_field`) rode a real
  `pipeline.json` for months with every gate green. `model_config` merges across
  inheritance, so a subclass adds `frozen=True` without restating `extra`. A model that
  must stay lax says so on itself and states why; the ledger's `models_lax` counts them.
- Frozen Pydantic models default; lineage via `derive()`, never mutation.
- Pure: no I/O, no `BackendClient`, no `Stores`. If a function needs
  infrastructure, it lives in `application/`.
