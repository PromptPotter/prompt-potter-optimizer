# M11 — Spend tracking

**Status:** Spec stub 2026-05-08. The webapp's spend chips and the
publication's cost/efficiency plot both need this. Listed as "Cost
tracking" in [`m12-plus-backlog.md`](m12-plus-backlog.md); pull forward
when prioritized.

## What's needed

Operator-facing surfaces (webapp bar, publication figures, log.md) need
one shared $-per-cycle number. Today there's none — `TokenUsage` is
captured per-call (`infrastructure/llm.py:113`) and discarded after a
warn-on-oversize check (`emit_token_usage`).

## Direction

- Aggregate `TokenUsage` per cycle, not per call. One aggregator, one
  number, all consumers read it.
- Three-layer rate resolution at `shared/spend.py` (LiteLLM-style; same
  shape Langfuse / tokencost converged on):
  - **Wire passthrough** — OpenRouter's `usage.cost` short-circuits the
    rate table; matches the operator's invoice.
  - **Runtime cache** at `~/.promptpotter/rates.json`, wrapped as
    `{"fetched_at", "models"}` with a 24 h TTL. CLI calls
    `refresh_rates()` on `optimize` start; no-op when fresh.
    `--refresh-rates` forces.
  - **Bundled floor** at `promptpotter/shared/data/rates.json` —
    checked into the repo, same wrapped format, bumped via PR. Loaded
    when no cache is present so a fresh install with no internet still
    resolves rates.
- Stdlib-only fetcher (`urllib`+`json`); no PyPI cost lib (March 2026
  LiteLLM incident). Payload capped at 8 MB; SHA pinning skipped — the
  bundled floor is the audited origin.
- Write `dashboard.json::spend = {used_usd, budget_usd, by_kind, calls}`.
  No second file.
- New `OptimizationConfig.spend_budget_usd: float | None`. Halt with a
  new `StopReason.SPEND_BUDGET` at round boundary when exceeded.
- Backend tokens come from the connector's wire response. If the
  connector doesn't surface them, `unknown_calls` ticks up and the bar
  shows the asterisk.

## Why one aggregator

Bar, publication, and log.md must agree to the cent. Re-summing in
each consumer guarantees drift.

## Out of scope

- Per-round / per-candidate spend breakdown.
- Cross-cycle accounting in `archive/`.

## Anchors

- `promptpotter/infrastructure/llm.py:113` — `TokenUsage`.
- `promptpotter/application/optimization/llm_call.py:209` — emit site.
- `promptpotter/infrastructure/projections/live_dashboard.py` — projection.
- `promptpotter/application/origin.py::build_campaign_emitter` — wiring.
