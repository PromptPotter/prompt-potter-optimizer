# w11-config3: area 07, round 3: one typed node document; params final once folded (cloud · Opus)

`briefs/common.md`, `briefs/w10-shared.md` and `briefs/w11-shared.md` bind you. SPEC: `briefs/map.md`
§ 07 and the debt doc's `## 07` rows "Params are not one fold", "The node document is typed two ways…",
"The pipeline carrier is typed only at its door" and "A knob's provenance is diffed, not read off the
fold". w11-config2 left these unbuilt for a design. The design below was read off the code at
`9075a02f4`; verify each claim before you build on it.

## Design

**The type.** `domain/pipeline_params.py::PipelineParams` (`steps`, `nodes: dict[str, NodeCall]`) is the
laid document. The missing type is the PATCH: `NodeCall._keeps_order` drops nulls and is strict, so it
cannot hold a clear or untyped text. Add `NodePatch` beside it as the value of every `Layer.patch`, so
only `fold_layers(node_rule(schema))` turns patches into a `PipelineParams`. The views become one:
- `search_point.py::NodeConfig` is deleted. It re-types `temperature`/`top_p` to `float` where `NodeCall`
  keeps `int | float`: that is the second typing.
- `ManifestNode.config` becomes a `NodeCall`, and `typed_config` goes.
- `ManifestNodeOverlay.config` holds a `NodePatch`.
- `node_config_schema` takes `FoldedParams`, not optional `values`/`sources` fallbacks.
- `L2Memory.steered` / `steered_layout` / `steered_onto` go: the steer is a `NodePatch` laid as a layer.
- `pipeline_parsing.py::merge_node_blocks` folds the DECLARATION, a different document: it stays.

**Four post-fold steps become layers.** `opt_search_point.py::OptSearchPoint.configured` runs
`strip_rendered_prompt`, `fold_output_contract` and `pin_default_routes`, and the candidate path
(`_written` → `lay_nodes` → `configured`) never enters `ParamLayers`. The four steps:
- The starting prompts: layer `origin_prompt` after `dataset`. Only the session subject lays it.
- The output contract and the pinned route: the last two layers, each a callable patch over the fold so
  far (`Layer.patch` allows it). A description key or a contract key becomes a null clear.
- Candidates then fold through `ParamLayers(evolved=…)`.

**Scoring.** `layer_fold.py::_unwrapped` leaves a two-arm union whole, so `CampaignConfig.scoring` is laid
whole. That is why two per-key merges sit outside the rule: `draft_patch.py::_folded_scoring`, and
`campaign_config.py::_started` / `_scoring_block`. Add one metadata, `ScalarAs("per_sample")`: the union's
mapping arm lays keys, a held scalar reads as that key, and a lone key collapses back. Add a `ScoringBlock`
model whose siblings exclude through `ScopedBy` (`matcher` vs `per_sample`, `per_cell` vs `dials`). The
no-op rule (a `dials` edit equal to `draft_criterion(running, terms).dials`) needs the connector's
`terms`, so it lives in the door's callable layer, not the rule. The edit folds over the full chain, never
the `draft.config` partial.

**The carrier.** `config_key` hashing `[(name, call.laid_keys())]` over `list[tuple[str, NodeCall]]` is
hash-neutral by construction: every stored address is already a `NodeCall` dump, and `stable_hash` sorts
keys. The `("", {"prompt_fields_id"})` pair fires only for a chain-less schema: one constant sentinel
address, never replayed.

## Decided (operator mode; recorded here)

- **Sentinel:** delete it, and refuse a chain-less schema at mint. Re-keys that one address, named.
- **A fork's `dials` over a locked `per_cell`** clears it, which is what `ScopedBy` gives. One rule at
  every door, so `_started` stops building a block `split_scoring_block` refuses.
- **A read subject's `origin_prompt` layer** raises on a missing template variable, as `bind_resolution`
  does.
- **The steer** is optimizer state (`optimizer_state`, architecture §0) laid at read. Measure whether
  laying it moves any key. If it does, name the move in the report; do not add a knob for it.
- **`NodeConfig`'s float coercion:** measure whether dropping it moves an identity hash (`--digests`).
  A move is named, never compensated.

## Links, deletion first

1. **Delete the sentinel**: the pair, `if params else []`, and `file_keys`' `or [entry.config_key]`.
   Door: re-hash every `measurements/configs/*.json` in the offline world and the gate's fixtures, check
   `config_key(node_configs) == stem`, and count the empties.
2. **Type the archive carrier**: `ArchiveConfig.node_configs`, `config_key`, `_cell_key` and `chain_keys`.
   Door: the `f50c75d3…` pin assert passes unchanged. No re-key.
3. **`RuntimeFailure.observed: PipelineParams`**: one shape, where today it is a node's call or the whole
   document. `observed_call` goes. It wipes campaign run data: count it, and name the wipe and its
   landing line. The archive is untouched.
4. **`ScalarAs` + `ScoringBlock`**: `_folded_scoring` and `_scoring_block` go.
5. **Three `ParamLayer`s**: `configured` folds through `ParamLayers`. `fold_output_contract(`,
   `_apply_starting_prompts(` and `pin_default_routes(` each grep 0 outside their layer's definition.
6. **Provenance off the stamps**: `config_map.py::_knob_rows` reads `layer_fold.py::Laid`'s stamp, and
   `arm_gate`'s default moves to `defaults.yaml`.
7. **The steer as a layer; `NodeConfig` deleted.** `git grep -c "steered(" -- promptpotter` is 0.

OWNS: `domain/pipeline_params.py`, `layer_fold.py`, `pipeline_schema.py`, `pipeline_manifest.py`,
`pipeline_overlay.py`, `search_point.py`, `opt_search_point.py`, `campaign_config.py`, `domain/failures.py`;
`application/pipeline_resolve.py`, `config_map.py`, `datasets/draft_patch.py`, `compiler.py`;
`infrastructure/store/measurement_archive.py`; `potter/records.py`, `potter/validators.py` and
`potter/dispatch/injections/wounds.py`; and their tests. Re-point importers anywhere, imports only.

Other chains running at the same time:
- w11-spend2 owns area 05's files and the spend folds.
- w11-jobs3 owns `jobs/**`.
- w11-connectors3 owns `connectors/**`. It moves connector tunables out of `settings.py` and does not
  touch `pipeline_overlay.py` or `layer_fold.py`. Its tunable route rides your overlay as it stands at
  base.
- w11-web3 owns `webapp/**`. Regenerate `types.generated.ts` and edit no component.

DONE WHEN:
- Each link is re-tested through its door, before and after.
- Equals-main (`offline_run.py --against`) names every move, and `--digests` names each key.
- The debt doc's four rows are closed or cut to their open half.
- `python scripts/gate.py` is green.
