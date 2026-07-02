# Origin / params / config consolidation

> Maintenance pass. Triggered by the L4 origin-scoring silent-skip bug (round-0
> `total=0` on `promptpotter-self`), which a three-axis code survey showed to be
> one symptom of a broader structural class. Goal: **remove the class, not patch
> the symptom** — and lower `complexity_ledger` doing it.

## The disease (one, not three)

`pipeline_params` is an under-typed `dict[str, Any]` mixing node-config dicts
with the reserved wire scaffold `steps`, and **no single seam owns "measure the
origin."** So:

1. **Shape-sniffing for capability.** "Is this a node-config or reserved?" is
   guessed via `isinstance(v, dict)` / `k == "steps"` at ~8 sites.
   `has_program` (`origin.py`) is that same guess applied to "is this
   scoreable?" — and it is the guess that silently skipped L4 (empty prose +
   node configs all `{}` → `has_program == False` → origin pass skipped →
   `total=0`, connector never reached).
2. **Duplicate origin-scoring seams.** Round-0 (`prepare_scoring_context`, gated
   by `has_program`) and per-round (`rescore_origin`, ungated) both measure the
   origin through `score_search_point` but diverge — the origin gate's *rescore*
   action fixes the bug by running the *other* path.
3. **Silent skips.** `has_program`'s empty return logs nothing; the skip was only
   caught three layers downstream by the `total<=0` health grade.

Cross-cutting operator directive: **no fallback chains; short chains; a bad
origin fails fast with one loud named cause** (`origin bad — can't start —
<cause>`), never a silent empty return that mimics a crash downstream.

## P1 — one reserved-key source + node-config accessor

The domain already half-owns this (`flatten_sp_summary` skips `steps`;
`to_pipeline_params` builds `{"steps": …}`) but there is no single accessor, so
each site re-implements the skip.

- **Add** to `domain/` (beside `OptSearchPoint` params helpers):
  `RESERVED_PIPELINE_PARAM_KEYS = frozenset({"steps"})` and
  `node_config_items(pp) -> Iterator[tuple[str, dict]]` (yields the node-config
  entries, skipping reserved keys + non-dicts). One named source of truth.
- **Route through it** (delete the ad-hoc guess at each):
  `opt_search_point.py::flatten_sp_summary`, `optimization/cycle.py:167`,
  `runner/entry.py:239` (seed overlay merge), `config.py:531` (override merge),
  `bootstrap/scoring_context.py:126` (target_models), `l1/population.py:54`,
  `presentation/views/display.py:360`, and the connector wire adapters
  (`connectors/llm_only.py:64`, `connectors/termnorm.py:51`) where they skip
  `steps` by hand.
- ≥8 sites, ≥1 concept removed → clears the extraction threshold and lowers the
  ledger.

`steps` itself stays in `pipeline_params` — it is the wire scaffold TermNorm's
outbound payload reads; lifting it out is a wire-shape change out of scope here.

## P2 — one origin-baseline seam, fail-fast

- **Delete `has_program`** (`origin.py:450-452`) and the not-scoreable early
  return. Its only live effect is the L4 false-negative; a genuinely empty
  param-only pipeline should not be running and must fail loud, not skip.
- **Collapse** round-0 origin scoring (`prepare_scoring_context`'s scoring half)
  and `rescore_origin` into **one** `score_origin(...)` seam through
  `score_search_point`. The origin gate's rescore already calls the correct one;
  round-0 must call the same.
- **Unify** the dual `populate_session_scoring` ("origin scorer built then
  overwritten") so round 0 and rounds 1+ are scored under one attach.
- **Fail-fast:** origin measures zero / all-error → one loud
  `origin bad — can't start — <cause>` halt (the round-0 gate, surfaced earlier
  and without the interactive silent-skip predecessor).

## P3 — config-resolution ordering — INVESTIGATED, NOT A LIVE DEFECT

The hypothesis: `build_origin_cycle_id` (mint) hashes dataset-merged params
while the seed's `pipeline_overlay` merges later at `entry.py::_prepare_run`, so
the cycle id could disagree with the params the run actually uses. On inspection
this cannot occur:

- `_campaign_origin_seed` (`mint.py:50`) and the checkin path
  (`launcher/checkin.py:209`) build `CycleSeed(origin_prompt_fields=…,
  origin_source="campaign_origin")` with **no `pipeline_overlay`** — a
  campaign-from-origin seed never mutates params after the id is derived, and the
  seed's *origin* is already fed into `build_origin_cycle_id` at mint.
- The only seeds carrying a `pipeline_overlay` are **operator forks**
  (`_mint_fork`), whose cycle ids are **parent-lineage-derived, not
  content-addressed** — there is no param hash to disagree with.
- Draft/ingest overlays are folded into the committed `pipeline.json` at commit
  (`draft_build.py::split_overlay`), not left in a seed.

`configure_and_apply_pipeline` is called from ~8 sites but it is *the* single
gateway (`config.py`: "the SINGLE definition of which node config a cycle id and
measurement key hash") — many callers of one gateway is not duplication.

**No code change.** Fixing a theoretical mismatch that no path produces would add
compensating machinery for a bug that isn't there.

## Ledger

Every phase removes ≥1 named concept (`has_program`, the per-site reserved
guesses, one of the two origin-scoring seams). `python -m
promptpotter.diagnostics.complexity_ledger` must end **lower**; ratchet the
`tests/test_complexity_ledger.py` baseline down to lock it.
