# cfg-2: the node document's one merge rule and the defaults' one home (Opus, cloud)

Read `briefs/common.md` on `origin/brief/wave-5` whole first; it binds you. Chain id `cfg-2`, arc `arc/cfg-2`.

- **Base:** `origin/main` at `e24dfda95` (v0.8.20; landing 50 = cfg: every layered document rides `domain/layer_fold.py`, `CLEAR` its reset value, every door lays its patch through the fold). Build on that, never beside it.
- **Spec:** the ledger section `### cfg-2 (what cfg left at landing 50; the re-key window)` of `docs/specs/code-debt-cleanup.md`, every row, plus the rows its second row says it closes (rk-run's cleared-steer-field row, "A relabel over a mixed concept", admit-2's untyped-steer row and "A retired knob refuses at the launch, not at the landing", and "Node config is one document read through ~11 modules"). Find each by its title.
- **This is the re-key window:** any digest may move and every L4 cell may re-key. Name each move; none is a failure.
- **Tenant data (grepped on the desk; you cannot see it):** the tenant datasets set only `campaign.degradation_threshold: 0.4` and `campaign.elimination_n_min: 4`, and they name no other knob this chain touches. Bounding `degradation_threshold` to 0..1 is safe.

## Decisions (orchestrator; build them, do not reopen them)

- **One wire rule for a patch, on every door:** an absent key leaves the value unchanged; `null` is `CLEAR` (the layer below shows through); `""` is the empty-string value, never a sentinel. The CLI `--set` / `--steer`, REST, the webapp, `embedded_run` and check-in all parse a patch through one function into the fold. The browser's `""` handling goes.
- **One typing:** every door types a value by its param's declared schema, as `launching.py`'s steer typing already does. `--set` stops typing values as YAML.
- **`--config`:** `authored.py::ConfigLayers.of` alone decides what a budget means. The CLI's pop goes, and REST and embedded give the same answer (parity).
- **The steered values are a named fold layer**, not a side memory.
- **One typed node document:** `CycleSeed`'s and `ForkSeed`'s overlays are one type, and knobs are a view on it.
- **Defaults:** every campaign default lives in `assets/datasets/defaults.yaml`, and each `CampaignConfig` field it covers is required. Every construction builds through the fold over that file; tests build through one factory. Name the `exclude_defaults` identity re-key.

## Links (one commit each, in order)

1. **A proposal carries the stripped overlay** (`node_context.py`'s `Proposal(overlay=…)` takes `carried_writes`' config). The patch check is then spelled once and raises; `PipelineParams.overlaid`'s heal goes. This may move L1's decisions on the potter leg: name it.
2. **One merge rule and one node document**, by the decisions above, including the field-order row: each reader takes the node's own field list, and `PROMPT_FIELDS` leaves the connectors and `pipeline_responses.py`.
3. **The defaults' one home** and the `--config` / `--set` row.
4. **Couplings derived and one validator** (the knob-couplings row): couplings come from each member's `draws` and the ladder enum; `degradation_threshold` is bounded 0..1; one `CampaignConfig` validator refuses a narrowing or overlay on an excluded node; the clamp's route is keyed by model.

## Beside you (cloud, running now)

- **life-2** shares two files, each by hunk:
  - `presentation/cli/commands/new.py`: yours are the `--set` typing and the `--config` budget pop; life-2's is the readiness check.
  - `application/commands/launching.py`: yours is the steer typing; life-2's are `admit` and the readiness check.

  Edit only your hunks and name each in the commit body.
- **Main also holds an unpushed commit** that touches `application/health.py`, `deploy-linux/update.sh`, `scripts/gate.py::_install_digest`, the end of `tests/test_security.py` and the ledger's `batch/31 · box` row. Leave those hunks alone.

## Proof (beyond common.md)

- The equals-main proof names every leg that moved and why: a move is expected only on links 1 and 3.
- Each row's Re-test, before and after.
- LEAN: the defaults move and the merge rule should shrink the inner layers. Report the number.
