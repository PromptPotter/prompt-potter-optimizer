# domain/ — frozen models + pure types

The pure layer: no I/O, no async; `scripts/gate.py::_LAYERING` refuses an import of `infrastructure/`,
`connectors/`, `application/` or `presentation/`.

## Backbone primitives

- **A target prompt has no class**: each prompt node declares its fields in render order
  (`NodePromptInfo.fields`), and that order sits inside every cell key the node renders, so it is the
  node's, never a shared constant.
- **`OptSearchPoint.loci()` is the one roster of its parts; a child is made by `derive` / `edited` alone**
  <!-- probe: git grep -cE "OptSearchPoint\(" -- promptpotter/*.py <= 5 --> — its `id` is its content,
  never stored, and no optimizer state, campaign framing or shot content rides it.
- **A field on `RoundOutcome` is ledger shape**: a rename erases every close carrying the old name.
  `scoreboard` is derived at each read, never on the ledger.
- **`ForkSpec.keep_rounds` names the act**: unset is `operator_steered` (an offshoot from the origin), set
  is `operator_rewind` (rounds `0..N-1` lifted). `mint_fork` is its one writer.
  <!-- probe: git grep -cE "ForkSpec\(" -- promptpotter/*.py <= 2 -->
- **`AbilityReading.comparable_to` is the only test for differencing two θ**, and
  `AbilityReading.minus` the one difference <!-- probe: git grep -cE "\.theta - " -- promptpotter/*.py <= 1 --> — a cell the `DeltaRuler` does not
  carry enters no θ (`ThetaCaveat.UNMEASURED_DELTA`).
- **`CycleHop` is the `(campaign, cycle)` address**: an object holding both exposes it as a property
  (`Campaign.root_hop`, `Session.hop`, `Job.hop`), never re-paired from two `str`s at a call site;
  `CycleDir` / `WorkspaceDir` are passed through, never rebuilt from `str`.
- **`model_capabilities` rides `PipelineSchema`, never the identity**, so a refreshed snapshot re-keys nothing.
- **A resume re-derives a `CheckpointKind` iff a replayer is registered for it**
  (`promptpotter/application/bench/resume_and_fork/replayers.py::replayers`).
- **`pipeline_overlay.py` is the one walk and merge of `pipeline_params`** — a re-derived `k == "steps"`
  is a second definition. <!-- probe: git grep -cE "== .steps." -- promptpotter/*.py == 0 -->
- **The estimator digest hashes `l4/proxies`**, so only what decides a cell's number lives there.
- **`spend.py::TokenAccount` IS the carrier** — never hand-convert it at a call site.

## Sanctioned `deprecated` vocabulary

**`deprecated` is domain language for the fatal-warning sample lifecycle only** — `is_deprecated`,
`deprecated_samples`, `CellFold.deprecated`, `retry_of_deprecated_cache`, the whole set; never a
back-compat shim.

## Inherit `StrictModel`, never `BaseModel`

**Every model inherits `StrictModel` (`strict_model.py`), a frozen one `FrozenModel`**; a lax model
says why on itself, and the `surface-ledger` row `models_lax` counts them.

## Tolerance is scoped by what a payload is FOR

`extra="forbid"` reaches every model nested in a `RoundStatement`, so **renaming a field wipes run
data**; what a file written before it meets depends on the payload:

- **Reporting** (`round_diagnostics.py`) — every field defaults; nothing gates on it.
- **Scoring** (`ArmState`, `StopSignal`, `OptSearchPoint`) — required; a tolerant read hands back a
  silently defaulted winner.
- **Ledger** (`CycleRecord` arms) — never tolerant (`infrastructure/store/read_model.py::ledger_record`):
  a line no arm accepts is `shared/errors.py::LedgerUnreadableError`, the refusal, never a repair.
