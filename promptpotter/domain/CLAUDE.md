# domain/ — frozen models + pure types

No I/O, no async; the import ban is `scripts/gate.py::_LAYERING`.

## Backbone primitives

- **A target prompt has no class**: field order is the node's (`NodePromptInfo.fields`), never a shared constant.
- **`OptSearchPoint.loci()` is the one roster of its parts; a child is made by `derive` / `edited` alone** <!-- probe: git grep -cE "OptSearchPoint\(" -- promptpotter/*.py <= 5 --> — its `id` is its content, never stored.
- **`ForkSpec.keep_rounds` names the act**: unset is `operator_steered`, set is `operator_rewind`; `mint_fork` is its one writer. <!-- probe: git grep -cE "ForkSpec\(" -- promptpotter/*.py <= 2 -->
- **`AbilityReading.comparable_to` is the only test for differencing two θ**, `AbilityReading.minus` the one difference <!-- probe: git grep -cE "\.theta - " -- promptpotter/*.py <= 1 -->; a cell the `DeltaRuler` does not carry enters no θ (`ThetaCaveat.UNMEASURED_DELTA`).
- **`CycleHop` is the `(campaign, cycle)` address**, exposed as a property (`Campaign.root_hop`, `Session.hop`, `Job.hop`), never re-paired from two `str`s.
- **`pipeline_overlay.py` is the one walk and merge of `pipeline_params`.** <!-- probe: git grep -cE "== .steps." -- promptpotter/*.py == 0 -->
- **The estimator digest hashes `l4/proxies`**, so only what decides a cell's number lives there.
- **`spend.py::TokenAccount` IS the carrier** — never hand-convert it at a call site.
- **`deprecated` is domain language for the fatal-warning sample lifecycle only** (`is_deprecated`, `deprecated_samples`, `CellFold.deprecated`, `retry_of_deprecated_cache`) — never a back-compat shim.
- **Every model inherits `StrictModel` / `FrozenModel`**; a lax one says why on itself (`complexity_ledger.py` row `models_lax`).

## Tolerance is scoped by what a payload is FOR

`extra="forbid"` reaches every model nested in a `RoundStatement`, `CampaignConfig` and `Campaign`, so **renaming a field wipes run data**. What a file written before the rename meets: **reporting** (`round_diagnostics.py`) — every field defaults; **scoring** (`ArmState`, `StopSignal`, `OptSearchPoint`) — required, since a tolerant read hands back a silently defaulted winner; **ledger** (`CycleRecord` arms) — never tolerant, a line no arm accepts is `shared/errors.py::LedgerUnreadableError`.
