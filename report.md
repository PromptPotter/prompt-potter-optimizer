# addr-4 report

Base `e8183708f` (origin/land/w1); origin/main last commit Thu Nov 19 17:40 2026 +0100. Arc `arc/addr-4` at `383c97118`.

Commits, promptpotter +/−: 1 `aea743e0a` +40/−27 · 2 `40e48d60a` +79/−123 · 3 `e29846d66` +3/−6 · 4 `7c716a647` +71/−79 · 5 `7f806f600` +10/−5 · 6 `f660ad28f` +23/−3 · 7 `8814c8818` +4/−10 · ledger `383c97118`: addr-3's raise +59 → +39.

**LEAN:** net −23. Links 1, 5 and 6 grow by their fix itself.

**Re-tests (before → after):**
- 1: a null knob lays back the manifest's value → refused; `unbounded` round-trips.
- 2: `_without_identity` in 1 file → 0.
- 5: an omitted effort or empty route keys its own reply → one key.
- 6: an unlayable list is emitted silently → `fold_unexpressible`.
- 7: a default potter campaign raises the `inert` coupling → it does not.

**Gate:** the full `scripts/gate.py` is green at the tip (30 checks). The first tip run had one playwright failure, which passed on rerun (Symptoms).

**Equals-main** (`/tmp/ha --against /tmp/hb`): every capo, capo-pobb, gepa, levi and potter leg is UNMOVED. potter/l4 MOVED. Its decisions lose only 10 `inner_origin` leaves (link 2), and no pick moved. Its requests are unmoved. Its inner requests move only by the fake nonce: the 29 bodies match once 8-hex values are masked.

**Digests** (bisected; both move at link 3, where `k_max` moved into potter's manifest): estimator `65cc26408ce71f10` → `f709c57234a4f062`, potter `5fc2308907883c5e` → `d7971389611ef043`, the rest unchanged. `behaviour_keys.json` estimator `e0f3be90` → `78e1d9f1`.

**Wipes:** run data, not migrated. `PartitionRecord.instrument_id` is now required, and a null knob is refused. This container held 0 campaigns.

**Decided in-run:**
- The no-bound spelling is `unbounded`, and a null `NodeCall` extra is unset.
- `ParamLayers.recorded` is deleted (its only reader was the identity case). A point's instrument is filed on `PartitionRecord`. This departs from the brief's "recorded keeps the identity".
- The preflight compares full instrument ids, read through `CampaignSurvey.instrument` with the survey's fault guard.
- `display_metric: ability` is the default, as F9's root is a defaults value.
- Outside Owns, with no collision: the instrument's readers (`evidence/`, `runner/bench.py`, `runner/mint.py`, `closed_rounds.py`, `partition.py`, `earned_blocks.py`), the potter knob users, `pipeline_params.py`/`pipeline_schema.py`, and `run_spec`'s callers.

## Rows closed
- **An identity re-key moves offline decisions** (link 2)

## Rows to file
- **`instrument_of` takes params it reads only identity from**: `domain/paired_reading.py::instrument_of(dataset_hash, params)` reads only the inner origin. Since link 2 that is cell identity, never sent params, so its callers lay `PipelineParams.filed(cell_identity)` to call it. Action: it takes the cell identity. **Rides with:** bkeys (owns the file). **Re-test:** `git grep -c "filed(" -- promptpotter/application/runner/mint.py` == 0.

## Symptoms
- `webapp/e2e/walk/responsive.spec.ts:55` (phone-412): "a tap lands off the control drawn there", because a `rowhover-shell` card covered the rows. It passed on rerun, and 375/393 passed in the same run. Suspected root: the previous test's hover card does not close before the tap probe.
