# M10: Prompt-Iteration Framework + L1-generate Tuning

**Status:** partially-shipped. Sweep toolkit + unified fork primitive + behavior checks wired. Exit gate (`rounds_to_95 ≤ 5`, `proxy_lift_corr ≥ 0.6` over ≥4 paired branches) not yet met. Live iteration mechanism: `/potter-l1-meta-campaign` skill + [`../developer/l1-meta-campaign.md`](../developer/l1-meta-campaign.md).

**Goal:** `rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`.

## What this covers

Tune the four optimizer meta-prompts (`l1_generate`, `l1_critique`, `l2_context`, `l3_plan`) by giving the operator + Claude per-cycle structured feedback. `l1_generate` is the principal bottleneck — the loop only descends gradient when L1 produces useful variants. Auto-tuning (L4) is too expensive at this scale; the framework's job is to give the operator the same quality of feedback an L4 would consume.

**L4 partial.** `proxy_lift_corr` validates the cheap proxy, the unified fork primitive is the cheap-trial mechanism, behavior checks are the conformance signal, `review.md` + `L1Stats` are per-cycle features. PromptPotter-as-backend (M12+) is the eventual L4 driver.

## Open tracks

- **Behavior checks** (`application/optimization/validators/l1_behavior.py`) — `context_object_honored`, `param_scope_discipline`, `not_only_param_variants`, `evidence_grounding_present`, `optimizer_rewind_guard` (wired with L2/L3 rebase). Pure `(round_dict, ctx) → CheckResult`. Adding a check is one-function diff.
- **Per-cycle `review.md`** — pure renderer at `application/review.py`; per-round L1 inputs, behavior checklist, variants table, critique, next-gen peek (sweep mode). Header carries the four prompt-template hashes + `L1Stats` block + round-1 verdict.
- **`L1Stats`** — `rounds_to_95` (headline), `round_1_verdict` (gate signal, conformance-anchored), `yield_rate`, `top_lift_mean`, `behavior_pass_rate`, `stagnation_max`, `l2_fires`, `proxy_lift_corr` (cross-cycle). Round-1 verdict: zero conformance ✗ → healthy · one ✗ → degraded · ≥2 ✗ or persistent `forbidden_axes_honored` → broken.
- **Cross-cycle leaderboard** (`application/leaderboard.py` + `scripts/ppot_review.py`). Read-only stdout; row keyed on `(l1_generate_hash, rounds_to_95 asc, behavior_pass_rate desc)`. `--sweep` filters to `fork_trigger=operator_sweep`.
- **Unified fork primitive.** `_mint_fork(parent, fork_from_round, payload)` is the single entry point for all 6 `ForkTrigger` variants (`OPERATOR_SWEEP` / `OPERATOR_REWIND` / `OPERATOR_DIAG` / `L2_REBASE` / `L3_REBASE` / `SCORING_DIVERGENCE`); payload is a single `ForkPayload` shape across all triggers. Rebase emission **wired**: L2/L3 emit `fork_proposal: ForkProposal | None` on their output schema; the executor's post-apply hook stashes it on `cycle.rebase_request` and raises `StopLoop(StopReason.REBASED)`; `runner.entry` resolves the request post-finalize into `_mint_fork(L{2,3}_REBASE, ...)` + observer rebuild + automatic loop re-entry on the new fork (capped at `MAX_AUTO_REBASES = 10` per CLI invocation). Operator-side `resume --rewind N "reason"` mints an `OPERATOR_REWIND` sibling at round N. The capability is gated by `OptimizationConfig.rebase_capability` (default on) via the `rebase_capability` injection so ablation runs render the same L2/L3 prompts as a no-rebase build.
- **Sweep toolkit** — `time-to`, `round1`, `round2`, `slice`, `rank`. Result JSONs under `archive/sweeps/{l1_meta_prompt_hash}/{dataset}/{verb}_{ts}.json`; `rank` reads them. Toolkit shipped 2026-05-11 (commit `2da5054e`).
- **L2 self-diagnosis surface** — four new `INJECTIONS` entries: `l1_considered_mutations`, `axis_exhaustion`, `sample_delta`, `l1_verbosity_stats`. Plus low-priority `prompt_field_above_verbosity_threshold` escalation rule (fires `CONTINUE`, surfaces signal only). Composite-score verbosity penalty is M12.
- **Methodology doc** — `docs/methods/manual-prompt-tuning.md` (diagnosis tree, single-cycle cadence, sweep workflow).
- **L2 Imagination** *(deferred — would amend §0's four-LLM-calls invariant to five)*. Only fires after the tracks above are live and we've measured whether better panels close the gap. Gated by `campaign.json::optimization.l2_imagination = True` (default off).

## Exit gate

`rounds_to_95 ≤ 5` on `llm_only` AND TermNorm under the same `l1_generate_hash`; `behavior_pass_rate = 1.0` for seeded checks; `/potter-l1-meta-campaign` catches at least one prompt regression on a re-run before round 2; proxy decision recorded (`proxy_lift_corr ≥ 0.6` over ≥4 paired branches, or rules modified per validation procedure); unified `_mint_fork()` covers both scoring-divergence and operator-sweep callers under one path.

## Code surface

| Area | Files |
|---|---|
| Optimizer prompts | `application/optimization/optimizer_pipeline.json::resolved_prompts['{l1_generate,l1_critique,l2_context,l3_plan,checkin}/1']` |
| Output schemas | `optimizer_pipeline.json::resolved_schemas['{l1_generate,l1_critique}/1']`; `build_l1_output_schema` grafts target-node properties |
| Per-round trace | `campaigns/{cycle_id}/.cache/rounds/round_NNNN.json` (target) + `rounds/round_NNNN.json` (optimizer state) |
| Prompt snapshots | `campaigns/{cycle_id}/prompts/optimizer_prompt/{hash}/` |
| Compile path | `dispatch/llm_call/prompts.py::load_optimizer_prompt` → `DispatchHub.fill_l1`/`fill_fixed` → `compile_prompt` |
| Fork substrate | `domain/run_records.py::ResumeCheckpointKind.FORK_CUT`; `infrastructure/ledger.py::CycleEventLog.inherit_from`; `application/optimization/cycle.py::_fork_at_divergence` (→ `_mint_fork`); `presentation/cli/commands/resume.py::--fork-on-divergence` |
| Skills | `.claude/skills/potter-run/SKILL.md`, `.claude/skills/potter-l1-meta-campaign/SKILL.md` |
| Parity test | `tests/test_invariants.py::PER_CYCLE_OPERATOR_ARTIFACTS` |

## Out of scope

Auto-tuning prompts (L4) → [`m12-multi-connector.md`](m12-multi-connector.md) Track 4 · test-set validation → M11 · third pipeline → M12 · webapp surfacing → M11/M12 · composite-score verbosity penalty → [`m12-multi-connector.md`](m12-multi-connector.md) Track 5 · L1 meta-prompt decomposition (separate spec).
