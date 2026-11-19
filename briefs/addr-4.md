# addr-4: addr-3's fix pass, and its unbuilt row (cloud · W1)

`briefs/common.md` binds you (read BASE FIRST), with ONE override: the base is **`origin/land/w1`
at the sha the dispatch names**. That branch is main plus addr-3 (02bda7aad, one combined commit)
plus bkeys-3, both reviewed: W1, the re-key window, collects there and lands on the operator's word
when all of it is green. Push to `arc/addr-4`.

**You are in W1** (operator 2026-11-19): every backbone shape drift lands now, while no user data
exists, batched so run data is wiped and L4 cells are voided ONCE. A root-id, arm-id, treatment,
reply-cache-key or behaviour-key move is the PURPOSE: name each old → new and the link that moved it.
A DECISION or REQUEST move in equals-main is a failure unless a row's own text says it moves
decisions; then attribute every moved pick to that row. **The operator's standard for this pass:
"we always want to go to the full perfect shape at the root"** — no transitional step, no bridge,
no "kept X, filed the root".

An independent review of addr-3 is `git show origin/brief/wave-5:briefs/addr-4.review.md` (F1–F10;
its probe scripts are not reachable, so reproduce each F in your own probe). Where this brief and
the review differ, this brief decides.

## Links, in this order
1. **A null knob keeps its meaning on every layer** (review F1, HIGH). Today `layer_fold.py::
   patch_between` spells "this leaf is null" and "drop this key" the same way, so a declaration's
   null knob (e.g. `potter/knobs.py::TranscriptKnobs.transcripts: null`, "show every one") is laid
   back as a clear and the manifest's value runs while the root id names the null. **Decided:**
   null means CLEAR on every layer (door patches already read it so); a knob whose value today needs
   null to mean "every / whole / unbounded" gets an explicit non-null spelling in its own type, and
   every yaml, doc and test that spells it moves with it. Find every such leaf (knob classes,
   `NodeCall` extras in `pipeline_overlay`) before you choose the spelling; one spelling for all of
   them. Add the null-knob case to `test_a_declaration_lays_back_what_it_dropped_from_the_defaults`.
2. **An identity re-key moves offline decisions** (§ batch/29 · xconn, ledger row; addr-3 cut it,
   review F2). An arm's id is what it sends: a point's `pipeline_params` hold only what it sends; the
   identity is laid only into the archive key (`scoring/search_point_scorer.py`'s `node_configs`,
   `scoring/passes.py`, `datasets/loaders.py`), and `ParamLayers.recorded` keeps the identity a
   measured point ran under. `scoring/sample_measurement.py::_without_identity` and the identity case
   in `pipeline_resolve.py::_layer` go. Arm ids move (the purpose). Leave that section's TermNorm
   wire-contract row alone (W3). **Re-test:** `git grep -c _without_identity -- promptpotter` is 0,
   and an identity-only change moves no arm id and no offline decision.
3. **potter's `k_max` has one home** (review F4): `k_max: 0` in `optimizers/potter/pipeline.yaml`,
   the `defaults.yaml` `nodes` entry and the test's `nodes` filter deleted, `defaults.yaml`'s header
   true again; justlogic keeps naming its own `5`. And (review F5) promptpotter-self's
   `inner_campaign` (`research/datasets/promptpotter-self/family.yaml`, beside `n_variants: 3`) names
   `k_max: 0`, so no L4 inner cell on justlogic badges `shots_without_demo_pool`.
4. **An arm plan has one shape** (review F8): `CyclePlan.spec: RunSpec` plus `purpose: Purpose |
   ArmSeat`; `ArmSpec`, `spec_of`, the `spec` property and `_spec`'s caller-set `compare` flag go
   (`_spec` derives it from the purpose). Numbering stays exactly once at the mint.
5. **The reply key hashes what is sent** (review F6): `llm_reuse_cache.py` hashes
   `sent_effort(request.reasoning_effort)`; an empty route is normalized once where `ChatRequest` is
   built, deleting the `or None` at `bench/llm_call.py:281,358` and `connectors/harbor.py:1217`.
6. **The fold refuses what it cannot express** (review F7): `patch_between` raises on a SUBSET list
   that widens or a UNION list that is not `under` plus extras, with a test for each.
7. **Dead and stale** (review F9): delete `layer_fold.py`'s unreached CALL `route_order` re-add;
   `domain/campaign.py::Environment.target`'s docstring says what it now is (a fold patch whose null
   clears). Then addr-3's symptom: a default campaign raises the `inert` coupling
   `display_subset_relative_under_resubset` — find its root; if it is a `defaults.yaml` value, fix it
   here (this window re-keys anyway); if not, file it with its root.

## Owns
Everything addr-3 owned (`datasets/authored.py`, `domain/layer_fold.py`, `domain/command_kinds.py`,
`domain/campaign.py`, `jobs/mint.py`, `jobs/launcher/head_to_head.py`, `application/preflight.py`,
`pipeline_resolve.py`, `assets/datasets/defaults.yaml` and dataset yamls, `store/llm_reuse_cache.py`)
plus link 1's knob classes and their yamls, link 2's holders (`opt_search_point.py`,
`scoring/sample_measurement.py`, `initialization/session.py`, `scoring/search_point_scorer.py`,
`scoring/passes.py`, `datasets/loaders.py`), `optimizers/potter/pipeline.yaml`,
`research/datasets/promptpotter-self/family.yaml`, `bench/llm_call.py`, `connectors/harbor.py`,
their tests. Beside you runs **bkeys-4** (`optimizers/nodes.py`, `maintenance/landing.py`,
`domain/paired_reading.py`, `domain/results.py`, `domain/arm_reading.py`, `scripts/offline_run.py`,
`scripts/offline_world.py`, `scripts/build_ts_types.py`, `webapp/`, the round's response model) and
the webapp lane **webd-4** (`webapp/` CSS and components). Do not edit theirs; a needed edit there
goes to "Rows to file". `assets/behaviour_keys.json` and the generated specs: regenerate by their
scripts; the orchestrator regenerates once more at the landing.

## Proof
Equals-main per `common.md` against the base, **every leg including potter/l4 stated explicitly**
(review F3): decisions UNMOVED (a moved pick attributed to its row, or the link fails); requests
unmoved or moved only by an id/nonce the row names; every digest and id move named old → new.
Full gate green. **surface-ledger:** the base carries a provisional `addr-3 (+59)` raise in
`scripts/complexity_ledger.py`; hunt deletions in what you own, and leave the raise at what remains,
its comment naming what the lines buy. Report on `out/addr-4-report` (per link the commit and
+added/−deleted; re-tests before/after; digest moves; wipes; decided in-run; Rows closed; Rows to
file; Symptoms).
