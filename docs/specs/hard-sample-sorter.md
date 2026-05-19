# Spec: Hard-Sample Sorter

**Status:** Phase 1 (seed). Phases 2 and 3 unscheduled; ship opportunistically alongside M11 webapp work.

---

## Context

The exploration/exploitation sample-selection policy in [`../methods/exploration-exploitation.md`](../methods/exploration-exploitation.md) already fits a Rasch IRT posterior on every campaign, producing two first-class per-item quantities:

- **`δ_s`** — per-sample difficulty. Surfaces today only at end-of-cycle in `campaigns/{cycle_id}/hard_samples_campaign.json`, computed by `build_hard_samples_artifact()` (`promptpotter/application/intelligence/hard_sample_sorter.py`).
- **`θ_c`** — per-candidate ability. Surfaces today only inside the same Rasch fit, not exposed anywhere.

These two arrays, plus the raw `(candidate_id, sample_id) → hit` matrix they are fit on, are the core outputs of a **standalone capability that stands on its own outside the optimizer**: feed it a dataset and a handful of candidate prompts, get back a difficulty-ranked sample list and a candidate×sample performance matrix. That is useful even to users who never want the full L1/L2/L3 loop. It is also a natural product surface — "point PromptPotter's sorter at your dataset and tell me which samples are genuinely hard and which prompts handle them" — sellable independently from the optimization engine.

Today the infrastructure is almost there; only the **exposure** is missing. This spec ships the first-class data primitive (phase 1) and draws the end-state view (phase 3) so phases 2 and 3 have a stable target to aim at.

---

## Three-phase path

### Phase 1 — seed (this commit)

- Data primitive: `promptpotter/application/intelligence/hard_sample_sorter.py::build_hard_samples_artifact(rounds, ...)` — fits Rasch, resolves the spec's axis sort contract, returns a dict with `candidate_order`, `sample_order`, `cells`, and a `rasch` posterior view. Capped to top-K on disk; pass `top_k_*=None` for the full matrix.
- Narrative reframe of the methods doc so the sorter reads as "the other half" of exploration/exploitation, not an ad-hoc export.
- This spec file.

Nothing else. No rendering. No CLI. No new persisted artifact. The primitive exists so phases 2–3 have one import site and one shape to consume.

### Phase 2 — CLI + notebook ASCII heatmap

Intermediate checkpoint. The compact ASCII heatmap of the candidate×sample matrix is rendered inline into `log.md` at finalize (and at round boundaries when the digest regenerates). Reuses the phase-1 primitive plus the existing Rasch fit held in `EvolveResult.rasch`. Lives in `presentation/views/log_md.py::render_hard_sample_heatmap`.

### Phase 3 — webapp heatmap

Belongs under the **M11 webapp read-only track** (see [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)). Visual design + interactions decided there, not here. Consumes the same phase-1 primitive via the FastAPI read-only API. This spec deliberately does not pre-decide color tokens, zoom, filters, or hover behavior — those age badly without a live UI shell.

---

## Data contract (phase 1)

```python
from promptpotter.application.intelligence.hard_sample_sorter import (
    build_hard_samples_artifact,
)

artifact = build_hard_samples_artifact(cycle.rounds, top_k_candidates=None, top_k_samples=None)
# artifact["candidate_order"]:       list[str]              (θ_c desc)
# artifact["sample_order"]:          list[int]              (δ_s desc, hardest first — for the heatmap)
# artifact["cells"]:                 list[{"c", "s", "hit"}] (measured only)
# artifact["rasch"]:                 {"theta", "theta_se", "delta", "delta_se", ...}
# artifact["pick_score"]["sample_order"]: list[int]         (Fisher info desc — descriptive snapshot)
# artifact["pick_score"]["per_sample"]:   dict[str,float]   (Fisher info per sample, ≥0)
```

The `pick_score` block is a **descriptive snapshot** for the webapp dataset table and the FastAPI `/datasets/{name}/preview` endpoint. It carries 1PL Fisher information `p(1-p)` evaluated at `θ = 0` (the Rasch identifiability anchor / population-mean ability) — "how informative measuring this sample would be on a brand-new candidate before any of its outcomes land." High at samples whose δ is near 0 (the population-mean ability sees roughly 50/50), low at unanimous-easy and unanimous-hard tails symmetrically.

The **live picker** (`promptpotter/application/intelligence/adaptive_picker.py`) does NOT consume this snapshot. It maintains a per-candidate posterior on `θ_c` and re-picks per measurement under the configured objective (`mfi` or `track_and_stop`). Two consumers, two sorts:

- **Heatmap (`sample_order`, δ_s desc):** hardest first — operator sees the failure cluster aligned left.
- **Live picker (`adaptive_picker.next_sample_*`):** sample selected per step against the candidate's running θ̂_c posterior. See [`../concepts/paired-sample-pobb.md#sample-selection`](../concepts/paired-sample-pobb.md#sample-selection) for the objective contract.

**Tri-state cell.** A cell is *measured & hit*, *measured & miss*, or *absent* (unmeasured). Heatmap renderers iterate `cells` for the measured pairs and treat any `(c ∈ candidate_order × s ∈ sample_order)` not present as the unmeasured tier.

**Companion arrays** ride on the artifact's `rasch` block (sourced from `RaschPosterior`):

- `rasch["delta"]: dict[str, float]` — `δ_s` per sample (string-keyed for JSON; cast back to int).
- `rasch["theta"]: dict[str, float]` — `θ_c` per candidate.
- `rasch["delta_se"]`, `rasch["theta_se"]` — Laplace standard errors.

The renderer composes the artifact's cells + posterior view; no new aggregation layer is introduced.

---

## Heatmap wireframe (phase 2 / 3)

```
                  hardest ────────────── sample_id ──────────────→ easiest
               ┌───────────────────────────────────────────────────────┐
best  c_best   │  ██  ██  ▒▒  ██  ██  ██  ██  ██  ▒▒  ██  ██  ██  ██  │
 ↑    c_002    │  ██  ▒▒  ██  ██  ▒▒  ██  ██  ██  ██  ██  ▒▒  ██  ██  │
 │    c_003    │  ▒▒  ██  ██  ▒▒  ██  ██  ▒▒  ██  ██  ██  ██  ██  ██  │
θ_c  c_004    │  ░░  ▒▒  ▒▒  ██  ▒▒  ██  ██  ▒▒  ██  ██  ██  ██  ██  │
 │    c_005    │  ▒▒  ░░  ░░  ▒▒  ▒▒  ██  ██  ██  ▒▒  ██  ██  ██  ██  │
 ↓    c_worst  │  ░░  ░░  ░░  ░░  ▒▒  ▒▒  ██  ██  ██  ██  ██  ██  ██  │
worst          └───────────────────────────────────────────────────────┘
                  δ_s ↓
               legend:  ██ hit   ▒▒ miss   ░░ not measured
```

**Axis sort contract:**

- **Y-axis (candidates):** descending by `θ_c`. Tie-breaker: descending mean hit rate over measured cells. Final tie-breaker: candidate_id lexicographic.
- **X-axis (samples):** descending by `δ_s` — hardest on the left. Tie-breaker: descending miss rate. Final tie-breaker: sample_id ascending.

**Cell states:**

- Hit (measured and passed). Renderer choice: `█` block, green.
- Miss (measured and failed). Renderer choice: `▒` block, red.
- Unmeasured (`matrix.get(...) is None`). Renderer choice: `░` light block, grey.

The expected visual is red concentrated at bottom-left (worst candidates on hardest samples) and green concentrated at top-right (best candidates on easiest samples). Grey increases on the margins where the scoring-set evolution policy hasn't asked that cell yet — the unmeasured mass is a direct visual of the exploration/exploitation frontier.

The ASCII renderer (phase 2) must gracefully downgrade wide matrices: cap the visible X-axis at the top-K hardest samples, pass the remainder to a truncation footer (`... +N easier samples elided`). Candidate axis stays uncapped for the common small-N case.

---

## Why "hard-sample-sorter" is a standalone product

Two-sentence positioning:

> Every optimizer that evaluates `K` candidates on `N` samples produces a `K × N` hit matrix as a byproduct. The hard-sample-sorter makes that matrix — plus its Rasch-derived difficulty ranking — the primary output rather than a discarded intermediate, giving users a data-quality / dataset-curation tool that works even when they don't want the full optimization loop.

Licensing / packaging / pricing are out of scope for this spec; record here only as the product-framing reason the capability gets its own module and doc rather than living inside `scoring_set.py`.

---

## Open questions

- **δ_s persistence.** The full Rasch fit (incl. `delta` for every sample) is persisted at every round-end finalize as `campaigns/{cycle_id}/hard_samples_campaign.json` (this cycle's rounds) and `hard_samples_workspace.json` (cycle + archive observations), via `build_hard_samples_artifact()` from `presentation/writers.py`. Read by the webapp via `/datasets/{name}/preview` and rendered inline into `log.md`. No per-round in-trace persistence — the end-of-round artifact is the source of truth.
- **Cold cells.** Unmeasured cells have no Rasch estimate. Phase 2/3 may add a uniform-exploration tier that forces coverage of a small fraction of unmeasured cells before declaring the matrix stable. Out of scope in phase 1.

---

## References

- Methods doc: [`../methods/exploration-exploitation.md`](../methods/exploration-exploitation.md)
- Scoring-set evolution mechanism: `promptpotter/application/intelligence/exploration.py`
- Rasch posterior: `promptpotter/application/intelligence/exploration.py::RaschPosterior`
- Downstream webapp home (phase 3): [`m11-publication-benchmarks.md`](m11-publication-benchmarks.md)
