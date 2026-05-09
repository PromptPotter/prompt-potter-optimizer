# M12: PromptPotter-as-Connector — Optimizer-of-the-Optimizer

**Version:** 0.1.0
**Date:** 2026-05-09
**Status:** Spec — Phase B of the strategic-flaws milestone allocation
**Depends on:** `m12-multi-connector.md` Track 1 (connector boundary), `m11-publication-benchmarks.md` Track 5 (smoke-tested predecessor)
**Supersedes parts of:** M11 Track 5's trace-replay approach (real inner cycles instead)

---

## Context

The "orchestration is the product, backends are pluggable" claim has not been load-tested. TermNorm is the only registered connector today; the second slot is reserved for PromptPotter itself.

PromptPotter-as-connector is **the most urgent strategic item** in the milestone allocation (`/.claude/plans/1-i-have-no-steady-beacon.md`) because it:

1. Validates the connector boundary every other M12 deliverable rests on. Until two connectors exist, "pluggable" is unfalsified — you don't know which TermNorm assumptions leaked into `BackendClient`, the `extract_experiment` shape, or `split_query_parts`.
2. Unlocks composite-fitness work (Phase D / `m12-composite-fitness.md`). Multi-objective fitness is a cross-connector concern; designing it on a single connector bakes in TermNorm-shaped assumptions.
3. Produces the headline self-referential demo: **PromptPotter optimizes PromptPotter's L1/L2/L3/critique meta-prompts.**

The M11 Track 5 design (trace-replay against a fixed fixture) was a smoke-test scaffold. This spec replaces the inner shape with **real inner cycles** so the outer-loop signal reflects actual optimization improvement rather than fixture replay.

## Design

### Connector shape

`promptpotter/connectors/promptpotter.py` (new) — provides the five-hook `Connector` defined at `connectors/protocol.py:28-50`:

| Hook | Behavior |
|---|---|
| `name` | `"promptpotter"` |
| `wire_adapter(query, pipeline_params) -> payload` | `query` = inner-benchmark task identifier (dataset name + cycle params). `pipeline_params` carries the meta-prompt overrides being explored (the outer L1's mutation surface). Adapter constructs the inner-cycle config and returns a payload describing what to run. |
| `session_factory()` | Builds an isolated inner `Session` rooted under `.runtime/inner/<outer_round>/<sample_idx>/`. In-process, no external service. |
| `extract_experiment(experiment_data) -> (queries, index_terms)` | Maps "queries" to inner-benchmark tasks. `index_terms` is empty (no retrieval index). |
| `resolve_ground_truth(experiment_data, query) -> str \| None` | Returns the inner cycle's success criterion for that benchmark task — typically a target accuracy threshold. |

Self-registers via `register(Connector(...))` at import. `connectors/__init__.py` adds `"promptpotter": _PROMPTPOTTER` to the registry.

### Meta-prompt pipeline schema

`datasets/promptpotter-self/pipeline.json` (new) — describes the four meta-prompt nodes as `pipeline_params` keys:

| Node | Mutable fields (exposed as `pipeline_params`) |
|---|---|
| `l1_generate` | template fields (instruction, decomposition, output schema), dispatch-hub injection slot list |
| `l1_critique` | template fields (`negative_critique`, `suggested_axes` framing, score-narration prompt), injection slots |
| `l2_context` | template fields (refinement instruction, `task_context` merge policy), injection slots |
| `l3_plan` | template fields (replan trigger framing, plan-space description), injection slots |

Hand-built once; afterwards it's just config. The schema must validate against `optimizer_pipeline.json`'s pinned shape (parity test from `archive/m10-cleanup.md` §3.5).

### Three composable inner-cycle proxies

The operator composes inner-cycle scoring from three independently meaningful metrics. **All three are exposed simultaneously** — the operator scales weights through campaigns to accumulate evidence about which proxies correlate with publication-quality outcomes.

| Metric | Definition | Cost | Signal quality |
|---|---|---|---|
| `first_round_delta` | Inner-cycle score after round 1 minus baseline | Cheapest — one round per outer sample | Weak alone; useful for fast iteration on outer hyperparameters |
| `after_N_rounds_delta` | Inner-cycle score after `N` rounds (configurable, default `3`) minus baseline | Bounded — `N` rounds per outer sample | Captures improvement rate; the workhorse |
| `rounds_to_N` | Number of rounds to reach a target score (e.g. `0.80`); times out at `max_rounds` | Variable — fast on easy mutations, expensive on hard | Most truthful; closest to "did this meta-prompt actually help" |

Operators compose via `compile_scorer` (`promptpotter/shared/scoring.py`) — example formula in `campaign.json::scoring`:

```
0.4 * first_round_delta + 0.4 * after_N_rounds_delta + 0.2 * (1.0 / max(rounds_to_N, 1))
```

Names enter the formula scope flat (not `pipeline_data.first_round_delta`) — `compile_scorer`'s AST validator rejects `Attribute` access. The connector populates `pipeline_data` with the three proxy keys, and `_build_namespace` lifts them to top-level scope automatically.

The three names are scoped to the PromptPotter connector. When scoring a TermNorm result, those names are not in `compile_scorer`'s scope; the formula will raise at compile time. This is intentional — proxies are connector-specific, not universal.

**Operator workflow:** start with `first_round_delta` for fast iteration; add `after_N_rounds_delta` once outer-loop dynamics stabilize; switch to a `rounds_to_N`-weighted formula for publication runs. The three proxies accumulate as evidence — the operator never has to commit to one before the data is in.

### Inner-cycle isolation

Per-outer-sample sub-tree at `.runtime/inner/<outer_round>/<sample_idx>/`. Contains the inner cycle's full `.promptpotter/` tree (sessions, campaigns, archive). Inner cycles share no state across outer samples by default — each starts from the inner-baseline.

Cleanup policy: inner sub-trees retained for the outer cycle's lifetime (debugging), pruned at outer cycle finalize. Operator can opt out via `campaign.json::optimization.retain_inner_cycles: true`.

### Cost realism

Each outer "sample" is at minimum a partial inner cycle. Cost scales as:

```
outer_cost ≈ outer_n_samples × outer_n_candidates × inner_n_samples × inner_n_rounds × per_call_cost
```

For a typical `outer_n_samples=4`, `outer_n_candidates=3`, `inner_n_samples=10`, `inner_n_rounds=3` configuration, that's ~360 inner candidate-evaluations per outer round. Operators must size accordingly:

- **Development:** `inner_n_rounds=1`, `first_round_delta` only — order-of-minutes per outer round.
- **Calibration:** `inner_n_rounds=3`, all three proxies — order-of-tens-of-minutes.
- **Publication:** `inner_n_rounds=5`, `rounds_to_N`-weighted, target benchmark — hours.

The cost surfaces on the outer dashboard's `dashboard.json::spend` block (see `m11-spend-tracking.md`); operators read it before extending runs.

### Demo target

`datasets/promptpotter-self/` — a minimal cheap-proxy benchmark wired for development iteration:

- `pipeline.json` — the inner-cycle meta-prompt schema (above).
- `campaign.json` — composite scoring formula combining all three proxies; small `inner_n_samples`; conservative `inner_n_rounds=2`.
- `task_description.md` — the outer task's framing for L1: "improve PromptPotter's L1-generate meta-prompt for the GSM8K-small benchmark."
- Inner-benchmark dataset — small subset of GSM8K (or HotPotQA-small if BBEH-empty-predictions blocker still applies).

**End-to-end demo:** `python -m promptpotter optimize` with `--config datasets/promptpotter-self/campaign.json`. Outer dashboard at `:8001/ui/` shows PromptPotter optimizing PromptPotter, with the new PoBB posterior-width row (Phase A) tracking confidence on which meta-prompt mutation is winning.

## Non-goals

- **Distributed inner cycles.** Inner cycles run sequentially within the outer process. Parallel inner execution is webapp-Phase-2 / Control-remote work.
- **Fixture-based trace replay.** M11 Track 5's original design ran the connector against archived measurement traces. This spec replaces that with real inner cycles — the boundary test is more honest, the demo is more compelling, and there's no maintenance burden for keeping the fixture aligned with `optimizer_pipeline.json`.
- **Pareto-aware composite scoring.** Single composite formula via `compile_scorer`. True multi-objective (Pareto frontier in PoBB) is `m12-composite-fitness.md`.
- **Outer-loop convergence proofs.** This spec lands the connector and demonstrates one closed loop. Whether outer optimization actually improves inner meta-prompts on a publication benchmark is M12 Track 4 + a findings doc.

## Deliverables (in order)

1. Verify `Connector` protocol shape at `connectors/protocol.py:28-50` accommodates the five hooks above. Fix the protocol (not the connector) if a TermNorm assumption leaks.
2. `promptpotter/connectors/promptpotter.py` with five hooks; self-registers.
3. `connectors/__init__.py` registers the new connector.
4. Extend `compile_scorer` scope (`shared/scoring.py`) so formulas referencing `first_round_delta`, `after_N_rounds_delta`, `rounds_to_N` compile when the active connector is `promptpotter`.
5. `datasets/promptpotter-self/pipeline.json` — meta-prompt node schema.
6. `datasets/promptpotter-self/campaign.json` + `task_description.md` + small inner-benchmark dataset.
7. End-to-end demo run; outer dashboard renders.
8. Docs: `docs/concepts/optimizer-of-the-optimizer.md` (frame + three proxies); extend `docs/operations/persistence-and-state.md` with `.runtime/inner/` layout; update `promptpotter/connectors/CLAUDE.md` (or `infrastructure/CLAUDE.md`) with what the second connector taught about the boundary.

## Verification

- `init` an outer cycle pointing at `datasets/promptpotter-self/`. Confirm `pipeline.json` exposes meta-prompt nodes as `pipeline_params` keys.
- Run 1 outer sample with `n_samples=1`, `after_N_rounds_delta` as score, inner `N=2`. End-to-end: outer L1 generates a candidate meta-prompt; outer scoring runs an inner cycle on the cheap proxy; outer composite reports the delta.
- Combine all three proxies via `campaign.json::scoring` and re-run; confirm `compile_scorer` accepts the formula and values flow through.
- `python -m promptpotter optimize` with the PromptPotter connector on `datasets/promptpotter-self/`; outer dashboard at `:8001/ui/` shows PromptPotter optimizing PromptPotter.
- `tests/test_invariants.py::test_no_unexpected_runtime_layer_violations` green; no TermNorm assumption leaked into the new connector path.

## Cross-references

- `m12-multi-connector.md` Track 1 (connector boundary; this spec satisfies the "second connector" deliverable)
- `m12-multi-connector.md` Track 4 (outer-loop closure run; consumes this spec)
- `m11-publication-benchmarks.md` Track 5 (predecessor; trace-replay design superseded)
- `m12-composite-fitness.md` (depends on this spec landing first — composite fitness is cross-connector)
- `archive/m10-cleanup.md` §3.5 (parity test for `optimizer_pipeline.json` shape)
