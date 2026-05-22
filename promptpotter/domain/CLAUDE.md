# domain/ — frozen models + pure types

The pure layer. No I/O, no async, no infrastructure imports. Models are
frozen Pydantic; mutation isn't a thing the type permits. Lineage is
encoded by `derive()`.

## Backbone primitives

| Primitive | File | Why it's settled |
|---|---|---|
| `JobSearchPoint` | `search_point.py` | Frozen target spec, content-hashed via `content_hash(dataset)`. First positional arg to `score_search_point()`. |
| `PromptTemplate` | `opt_search_point.py` | 8-field prompt scheme with `render()` / `compile_prompt()`. Canonical prompts at `datasets/{name}/prompts/{node}.json`. |
| `OptSearchPoint` | `opt_search_point.py` | Optimizer state: lineage, L2/L3 overrides, per-individual memory, `task_context`, `plan`, `l1_layout`. **All new optimizer state flows through here** — no sidecar state. |
| `ResumeCheckpointKind` + `RESUME_CHECKPOINT_GATING` | `run_records.py` | Import-time exhaustiveness — adding a kind without a gating mode raises before the module loads. SoT for replayed-vs-archival gating. |
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

- `campaign.py` — `Campaign` frozen manifest (`campaign.json`); the
  first-class optimization-effort entity, single owner of the frozen
  `CampaignConfig` snapshot.
- `cycle_paths.py` — `CycleDir`, `SessionFamilyDir` newtypes (used by stores +
  projections; passed through, never reconstructed from `str`).
- `pipeline_parsing.py` — pure dict → `PipelineSchema` parser.
- `validators.py`, `phases.py`, `escalation_signals.py`, `round_diagnostics.py`,
  `scoring.py`, `connector.py`, `backend.py`, `sample.py`, `l1_layout.py` —
  domain types and pure logic shared across the application layer.

## Conventions

- PEP 604 type hints (`X | None`, `list[str]`).
- Fully `mypy --strict` — no override in `pyproject.toml`. New domain
  code passes strict: typed defs, parameterized `dict`/`list`, no `Any`
  returns. The pure core (`domain/` + `shared/` + `config/`) is the
  migrated strict zone; the I/O layers still sit behind the ledger.
- Frozen Pydantic models default; lineage via `derive()`, never mutation.
- Pure: no I/O, no `BackendClient`, no `Stores`. If a function needs
  infrastructure, it lives in `application/`.
