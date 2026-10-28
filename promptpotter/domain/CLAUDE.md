# domain/ — frozen models + pure types

The pure layer: no I/O, no async. Anything needing a `BackendClient` or `Stores` belongs in
[`../application/CLAUDE.md`](../application/CLAUDE.md)'s tree (`scripts/gate.py::_layering` enforces it).

## Backbone primitives

- **`JobSearchPoint`** (`search_point.py`) — the frozen target spec. Its cells are filed by its node
  chain; `content_hash(dataset)` names a campaign's root only.
- **`OptimizerPromptTemplate`** (`opt_search_point.py`) — a prompt the optimizer RUNS ON. **A target
  prompt has no class**: each prompt node declares its fields in render order (`NodePromptInfo.fields`),
  and that order sits inside every cell key the node renders, so it is the node's, never a shared constant.
- **`OptSearchPoint`** (`opt_search_point.py`) — the individual, `{locus: value}` over the pipeline's
  roster. **`loci()` is the one roster of its parts; a child is made by `derive` / `edited` alone.** Its
  `id` is its content, never stored. No optimizer state, campaign framing or shot content rides it.
- **`CheckpointKind`** (`run_records.py`) — the base of every decision-kind enum, so a kind names the party
  that took it. A resume re-derives a kind iff a replayer is registered for it
  (`application/bench/resume_and_fork/replayers.py::replayers`).
- **`ForkSpec` / `CycleSeed` / `ConfigOverrides`** (`run_records.py`) — the fork record, the starting point
  a non-root cycle begins from, and its config delta. **`keep_rounds` names the act**: unset is
  `operator_steered` (an offshoot from the origin), set is `operator_rewind` (rounds `0..N-1` lifted). One
  writer: `mint_fork`.
- **`PipelineSchema` / `PipelineNode`** (`pipeline_schema.py`) — parsed from `GET /pipeline`, with no backend
  constants. `model_capabilities` rides the schema, never the identity, so a refreshed snapshot re-keys nothing.
- **`RoundResult`** (`results.py`) — `RoundOutcome` plus its rows. **A field on `RoundOutcome` is ledger
  shape**: a rename erases every close carrying the old name. The `Election` holds the verdict; optimizer
  readouts ride `optimizer_state`; `scoreboard` is derived at each read, never on the ledger.
- **`AbilityReading`** (`ruler.py`) — θ, its δ scale and its caveat as ONE value, no field defaulted.
  `comparable_to` is the only test for differencing two θ; `theta_caveat` decides the caveat once.
- **`DeltaRuler`** (`ruler.py`) — the δ scale every θ is read on: one fit of the tenant's rows at one
  version, less the parent line's. A cell it does not carry enters no θ (`ThetaCaveat.UNMEASURED_DELTA`).

## Sanctioned `deprecated` vocabulary

**`deprecated` is domain language for the fatal-warning sample lifecycle only** — `is_deprecated`,
`deprecated_samples`, `CellFold.deprecated`, `retry_of_deprecated_cache`, the whole set; never a back-compat
shim. **`legacy` is never sanctioned.**

## Other surfaces

- **`l4/` is the L4 law's only home.** The estimator digest hashes `proxies`, so only what decides a cell's
  number lives there; identity and row facts sit beside it. `__init__.py` re-exports nothing.
- **`export.py`** projects the winner (the optimizer's declared pick, origin round included) with the
  provenance that makes its fitness readable. Pure, so it can grow no file read.
- **`spend.py`: `TokenAccount` IS the carrier** — never hand-convert it at a call site. `cache_share(*, replayed)`
  is the only reading of a prefix-cache discount, total over four states and served whole (`PrefixReading`).
- **`activity.py` is the one reader of a record as a line** (`ActivityFeed`), for the SSE tail and the time-ray.
- **`optimizer_state.py`** — the `OptimizerState` envelope every round banks; each `StatePart` subclasses in its
  owning package as a `StateSlice` or `RoundOutput`, and an unregistered kind raises.
- **`pool.py`** — a pool is a `StateSlice` of one kind from one `PoolSource`, banking what a reader draws.
- **`treatment.py`** is what an optimizer run IS; **`campaign.py`** the frozen manifest and its `RunDeclaration`.
- **`cycle_paths.py`: `CycleHop` is the `(campaign, cycle)` address, and an object holding both exposes it as a
  property** (`Campaign.root_hop`, `Session.hop`, `Job.hop`) — never re-pair two `str`s at a call site.
  `CycleDir` / `WorkspaceDir` are write targets, passed through, never rebuilt from `str`.
- **`pipeline_overlay.py`** is the one walk and merge of `pipeline_params`; a re-derived `k == "steps"` is a
  second definition. **`candidate_diff.py`** holds both "what changed" and "already tried", shared by every consumer.

## Inherit `StrictModel`, never `BaseModel`

**Every model inherits `StrictModel` (`strict_model.py`)**; a frozen one inherits `FrozenModel`. A lax model
says why on itself; the ledger's `models_lax` counts them.

## Tolerance is scoped by what a payload is FOR

`extra="forbid"` reaches every model nested in a `RoundStatement`, so a renamed field is fatal one way or the
other; which way is correct depends on the payload:

- **Reporting** (`round_diagnostics.py`) — every field defaults; nothing gates on it.
- **Scoring** (`ArmState`, `StopSignal`, `OptSearchPoint`) — required; a tolerant read hands back a silently
  defaulted winner.
- **Ledger** (`CycleRecord` arms) — never tolerant (`infrastructure/store/read_model.py::ledger_record`); a
  retired key is pruned by `application/maintenance/restamp.py::_prune_record`.

A renamed or newly required field cannot be pruned: `application/maintenance/restamp.py::check_readable` names each refused cycle's
campaign `delete`.

## Conventions

- **Frozen by default; lineage via `derive()`, never mutation.**
