# domain/ — frozen models + pure types

No I/O, no async (`scripts/gate.py::_LAYERING`). Every model inherits `StrictModel` / `FrozenModel`; a lax one says why on itself.

- **A target prompt has no class**: field order is the node's (`NodePromptInfo.fields`), never a shared constant.
- **A child `OptSearchPoint` is made by `derive` / `edited` alone**; `loci()` is the one roster of its parts and its `id` is its content, never stored. <!-- probe: git grep -cE "OptSearchPoint\(" -- promptpotter/*.py <= 5 -->
- **`ForkSpec.keep_rounds` names the act** (unset `operator_steered`, set `operator_rewind`); `cut_fork` is its one writer. <!-- probe: git grep -cE "ForkSpec\(" -- promptpotter/*.py <= 2 -->
- **Two θ are differenced only through `AbilityReading.comparable_to` / `.lift_over`**, and a θ lift (an `Estimate`) is judged only by `ruler.py::clears` <!-- probe: git grep -cE "\.theta - " -- promptpotter/*.py <= 1 -->; a cell the `DeltaRuler` does not carry enters no θ.
- **`CycleHop` is the `(campaign, cycle)` address** (`Session.hop`, `Job.hop`), never re-paired from two `str`s.
- **`pipeline_params.py::PipelineParams` is built only by reading the laid document it dumps back to**, so whatever it builds reads back; `STEPS_KEY`, `NodeCall`'s fields and `node_rule` are the one spelling of the key, the call-key rosters and the node fold's rule. <!-- probe: git grep -cE "== .steps." -- promptpotter/*.py == 0 -->
- **The estimator digest hashes `l4/proxies`**, so only what decides a cell's number lives there.
- **`spend.py::TokenAccount` IS the carrier**: never hand-convert it at a call site.
- **`deprecated` names the fatal-warning sample lifecycle only** (`is_deprecated`, `CellFold.deprecated`), never a back-compat shim: do not delete it as one.
- **A command kind is ONE `command_kinds.py::CommandKind` row** (here because the lazy CLI `--help` and a ledger fold cannot import the dispatcher): its payload model declares the kind, the row names `handler`, `capability` and `press`; spec, TS types and `BrowserKind` are generated, and a kind the browser presses takes its sender in `webapp/lib/api/commands.ts::commands`.

## Tolerance is scoped by what a payload is FOR

`extra="forbid"` reaches every model nested in a `RoundStatement`, `CampaignConfig` and `Campaign`, so **renaming a field wipes run data**. A file written before the rename meets: **scoring** (`ArmState`, `StopSignal`, `OptSearchPoint`), required, since a tolerant read hands back a silently defaulted winner; **ledger** (`CycleRecord` subclasses, `records/__init__.py::RECORDS`, read through `record_kinds.py`), never tolerant (`FileKind.LEDGER`).
