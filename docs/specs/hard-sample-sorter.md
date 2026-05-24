# Spec: Hard-Sample Sorter

**Status:** Phase 1 (seed) shipped. Phases 2 + 3 unscheduled.

> **`pick_score` contract is changing.** [`verdict-resolution-picker.md`](verdict-resolution-picker.md) drops the exploration term in the blended objective and unifies the persisted ranking with the live picker — one model, written to this same artifact whenever conditioning updates. The Phase 1 contract below describes today's behaviour; semantics change when that spec lands.

## What this is

A standalone capability: feed it a dataset + a handful of candidate prompts, get back a difficulty-ranked sample list (`δ_s`) and a candidate×sample hit matrix. Useful outside the L1/L2/L3 loop — a data-quality / curation tool — and sellable independently. Infrastructure already exists in `application/intelligence/`; only the exposure is missing.

## Phase 1 — primitive (shipped)

`promptpotter/application/intelligence/hard_sample_sorter.py::build_hard_samples_artifact(rounds, ...)` fits Rasch, resolves the axis-sort contract, returns:

```python
artifact["candidate_order"]                # list[str], θ_c desc
artifact["sample_order"]                   # list[int], δ_s desc (hardest first)
artifact["cells"]                          # list[{"c", "s", "hit"}], measured only
artifact["rasch"]                          # {"theta", "theta_se", "delta", "delta_se", ...}
artifact["pick_score"]["sample_order"]     # list[int], pick-value desc
artifact["pick_score"]["per_sample"]       # dict[str, float]
```

Persisted at every round-end finalize as `campaigns/{cycle_id}/hard_samples_campaign.json` (this cycle's rounds) + `hard_samples_workspace.json` (cycle + archive observations). Read by the webapp via `/datasets/{name}/preview` and rendered inline into `log.md`.

**Two consumers, two sorts.** Heatmap (`sample_order`, `δ_s` desc) — hardest first. Live picker (`adaptive_picker.next_sample`) — per-step against the candidate's running θ̂_c posterior; does NOT consume the persisted snapshot.

**Axis-sort contract** (renderers must honour): candidates Y-axis = `θ_c` desc, tie → mean hit-rate over measured cells desc, then lex; samples X-axis = `δ_s` desc, tie → miss-rate desc, then sample_id asc. Cells are tri-state: measured & hit · measured & miss · absent.

## Phase 2 — CLI + notebook ASCII heatmap (unscheduled)

Compact ASCII heatmap rendered inline into `log.md` at finalize + round boundaries. Reuses the Phase 1 primitive plus the live `EvolveResult.rasch`. Lives in `presentation/views/log_md.py::render_hard_sample_heatmap`. Wide matrices cap X-axis at top-K hardest with a truncation footer (`... +N easier samples elided`).

Expected visual: red concentrated bottom-left (worst candidates on hardest samples); green top-right; grey on the margins where the scoring-set policy hasn't asked that cell yet — a direct visual of the exploration/exploitation frontier.

## Phase 3 — webapp heatmap (unscheduled)

Belongs under the M11 webapp track ([`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)). Visual design + interactions decided there. Consumes the same Phase 1 primitive via the FastAPI read-only API.

## References

- Methods: [`../methods/exploration-exploitation.md`](../methods/exploration-exploitation.md)
- Scoring-set evolution: `application/intelligence/exploration.py`
- Rasch posterior: `application/intelligence/exploration.py::RaschPosterior`
