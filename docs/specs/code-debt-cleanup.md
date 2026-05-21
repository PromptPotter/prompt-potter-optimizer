# Code-Debt Cleanup — Optimizer-Layer Bloat + Dup'd Constants

Whole-codebase review for organically-accumulated tech debt — bloat,
parameter soup, duplication. Distinct from the crash-safety hardening
done in the same session (atomic file writes, silenced-swallow logging
in `replayers.py` / `llm_call.py`). This is a sequenced backlog, not a
single migration: each tier ships independently and the order is by
leverage, not dependency.

## What audited clean

The architecture itself is sound — this is not pervasive rot:

- Layer boundaries hold (`domain → application → infrastructure →
  presentation`); `application/intelligence/` does not import
  `application/optimization/`.
- `score_search_point()` is genuinely the single scoring gateway — no
  bypass paths.
- No sidecar optimizer state — everything rides `OptSearchPoint`.
- **Zero banned-pattern violations** — no shims, no fallback chains
  over renamed keys, no `legacy`/breadcrumb comments, no future-tense
  docstrings. Two `TODO`s repo-wide. The no-backward-compat discipline
  has held, and that is why the debt below stayed contained.

The debt is **concentrated bloat in the optimization hot-path** plus a
few duplicated constants.

## Problem — the bloat hotspots

200 files, ~44.6k lines. The size outliers, and what makes each big:

| File | Lines | What's wrong |
|---|---|---|
| `application/optimization/dispatch/hub/injections.py` | 1147 | 21 `_r_*` renderer functions in one flat module |
| `application/optimization/l1/score.py` | 1013 | four functions over 140 lines (see below) |
| `presentation/api/routers/campaigns.py` | 972 | one router, many endpoints + inline shaping |
| `infrastructure/projections/live_dashboard/view.py` | 799 | `LiveDashboardView` — 37 methods, touches every event kind |
| `infrastructure/store/campaign_store/store.py` | 789 | `CampaignStore` — 37 methods, 4 unrelated concerns |
| `application/optimization/dispatch/llm_call.py` | 686 | `llm_call()` ~240 lines / 11 kwargs; `run_optimizer_node()` 15 params |
| `application/scoring/search_point_scorer.py` | 646 | 8 classes, unclear loop-lifecycle orchestration |
| `application/optimization/cycle.py` | 512 | `Cycle` — 15+ ungrouped fields, mixed lifecycles |

`l1/score.py` is the worst single file — four oversized functions:
`decode_signal_effect` (~143 lines, 9 kwargs), `score_one_candidate`
(~186), `score_population` (~307, with a closure capturing 12+ vars),
`l1_score` (~211, ~16 params). It is also the file the live picker
work churns — every picker edit pays the bloat tax.

The **root cause** under the parameter soup: untyped nested dicts
(`dict[str, dict[str, Any]]`) are the lingua franca for
`pipeline_params`, `round_data`, `candidate_results`, threaded through
10+ call chains. With no types, callers bundle loose params and guard
with `.get()` ladders (115 `.get()` calls in
`presentation/views/view_ingress.py` alone).

## Tiers — recommended sequence

Order is by leverage. Tier 0 is free; Tier 1 is urgent because the
file churns; Tier 2+ are deliberate slices.

### Tier 0 — dup'd constants (S, zero behaviour change) — ✅ done 2026-05-21

- ✅ `PARAM_SCOPE_KEYS` (`validators/l1_behavior.py`) and
  `_NUMERIC_PARAM_AXES` (`dispatch/hub/injections.py`) were the
  **identical** frozenset `{temperature, max_tokens, reasoning_effort,
  top_p}` under two names — collapsed to one `PARAM_SCOPE_KEYS` in
  `domain/search_point.py`, next to `PARAM_FORBIDDEN_KEYS`.
- ✅ `_GSM8K_ANSWER_RE` was a verbatim-duplicated regex
  (`application/datasets.py`, `scoring/formula/matchers.py`) — one
  public `GSM8K_ANSWER_RE` in `matchers.py`. `datasets.py` imports it
  function-scoped inside `load_gsm8k`; a module-scope import would
  close a `datasets ↔ scoring` cycle (`scoring/__init__.py` →
  `search_point_scorer` → `datasets`).
- ✅ PoBB `epsilon = 0.05` was spelled in 6 places — one
  `POBB_DEFAULT_EPSILON` in `config/settings.py`, referenced by the
  `CampaignConfig.pobb_epsilon` Field, `PoBBConfig.epsilon`,
  `elevate_to_decisive`, the `--epsilon` CLI default, and the two
  `.get("epsilon", …)` fallbacks. **Correction to the earlier draft:**
  those two `.get` fallbacks do *not* read an always-present value —
  the PoBB `_leader_locked` result dict carries no `epsilon`, so the
  fallback genuinely fires on the leader-lock path. It stays; only the
  magic literal was replaced by the named constant.

### Tier 1 — split `l1/score.py` (L)

Highest leverage: worst file, actively churned by picker work.

- Bundle the candidate-independent params of `l1_score` and
  `decode_signal_effect` into dataclasses (`L1ScoringInput`, a signal
  decode-context) — kills the 9–16-param signatures.
- Extract round-winner selection out of `l1_score`.
- Lift `score_population`'s nested closure (`_pobb_backfill` and the
  picker-refit body) to module-level functions taking an explicit
  context — the 12-var capture is what makes the function unreadable.
- Target: no function over ~120 lines; the file splits naturally into
  `score.py` (orchestration) + a `signal_decode.py` + a picker-loop
  module.

### Tier 2 — structural, schedule deliberately (M each)

- **Split `injections.py`** into a `dispatchers/` subpackage, one
  module per injection category (wounds / diagnostics / context /
  examples / rules). Pure reorganization — the `INJECTIONS` registry
  stays the single seam, low risk.
- **Bundle `llm_call.py` params** — an `LLMCallContext` dataclass for
  the `(ledger, round_num, candidate_idx, cache)` plumbing; keep
  prompt/template args separate. Covers `llm_call()` and
  `run_optimizer_node()`.
- **Typed models for the nested dicts** (L, invasive — earns its own
  slice). Introduce `RoundData` / `CandidateResult` / a
  `PipelineParams` type and thread them through the scoring pipeline.
  This is the real prize — it dissolves the parameter soup and the
  `.get()` ladders at the source — but it touches many files, so it
  ships as its own deliberate cut, not folded into Tier 1.

### Tier 3 — god-class splits (M–L, lower urgency)

Stable, low-churn files — defer until they next need a change.

- `CampaignStore` (789 lines, 37 methods) — split by concern into
  focused sub-stores (manifest / cycle-index / round / candidate),
  compose under the existing class.
- `LiveDashboardView` (799 lines, 37 methods) — extract per-event-kind
  handlers; the class becomes a state machine + a dispatch table.
- `Cycle` (15+ fields) — group into run-config / loop-local /
  inter-round-bridge / cache sub-objects.

### Tier 4 — cosmetic, opportunistic (S)

- Seven classes named `*Context` / `*State` (`TenantContext`,
  `ScoringContext`, `CheckContext`, `_LoopContext`, `ReplayContext`,
  `CycleState`, …) — the uniform suffix masks distinct concepts;
  rename to semantic roles.
- `presentation/views/view_models.py` — 20+ view dataclasses
  re-declare `timestamp` / `round_num` / `cycle_id`; want a base.
- `task_context: dict[str, Any]` / `l1_layout: dict[str, list[str]]`
  at the L2 output boundary (`dispatch/schemas.py`) — works (validated
  downstream) but the key contract is implicit; a typed sub-schema or
  an explicit doc would harden it.

## What this is NOT

- Not a correctness fix. Nothing here changes behaviour; the
  crash-safety bugs found in the same review (atomic writes, silent
  swallows) were fixed separately and are not part of this backlog.
- Not an architecture change. Layers, the scoring gateway, and
  `OptSearchPoint`-as-sole-state all stay exactly as they are — this
  is bloat reduction inside the existing shape.
- Not a single milestone. Each tier is independent; Tier 0 can land
  today, the rest slot in as the relevant files are next touched.

## Status

- Backlog drafted 2026-05-21 from a whole-codebase tech-debt survey
  (three parallel deep-read passes: size/complexity, duplication,
  architecture).
- ✅ Tier 0 landed 2026-05-21 — ruff / mypy / pytest all green.
- Tier 1 is next, and should precede further `l1/score.py` feature work.
- Tiers 2–4 are unscheduled; pick them up opportunistically.
