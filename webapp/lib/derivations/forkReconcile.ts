// Fork reconcile defaults (decision E): when an operator steers a fork off a
// stopped/running cycle, the limit dialog defaults the fork's rounds + spend
// to the PARENT'S REMAINING — "you set 6 rounds, 3 are done, so 3 left" — then
// the operator confirms the fork's own ABSOLUTE ceiling. The continuation
// arithmetic lives here (pure data → data), sourced only from the polled
// `dashboard.json` the dashboard already shows: no parallel bookkeeping.
//
//   - rounds consumed = completed-round count (`dash.rounds.length`)
//   - parent ceiling   = `dash.run_limits.max_rounds` (written at INIT.exit)
//   - spend            = `readSpend(dash)` (used + cap from the spend block)
//
// A fork starts numbering at 1, so "remaining" IS the fork's default
// `max_rounds`. Floored at 1 — a fork that runs zero rounds is never the
// intent; the operator can still raise it before confirming.

import type { LimitOverrides } from "@/lib/api";
import type { DashboardSnapshot } from "@/lib/poll";
import { readSpend } from "./spend";

export interface ForkReconcileDefaults {
  // Completed rounds on the parent — the "3" in "3 of 6 used".
  roundsConsumed: number;
  // Parent's declared ceiling, or null when uncapped/unknown (run_limits
  // absent on older cycles). null → the rounds input starts blank (inherit).
  parentMaxRounds: number | null;
  // Default for the fork's absolute `max_rounds`: max - consumed, floored at
  // 1. null when the parent ceiling is unknown (operator types a value).
  roundsRemaining: number | null;
  // Spend already burned on the parent (USD), or 0 when none reported.
  spentUsd: number;
  // Parent's spend cap (USD), or null when uncapped.
  parentBudgetUsd: number | null;
  // Default for the fork's absolute spend cap: cap - spent, floored at 0.
  // null when the parent is uncapped (fork inherits uncapped).
  spendRemaining: number | null;
}

export function forkReconcileDefaults(dash: DashboardSnapshot | null): ForkReconcileDefaults {
  const roundsConsumed = Array.isArray(dash?.rounds) ? dash.rounds.length : 0;

  const rawMax = dash?.run_limits?.max_rounds;
  const parentMaxRounds = typeof rawMax === "number" ? rawMax : null;
  const roundsRemaining =
    parentMaxRounds != null ? Math.max(1, parentMaxRounds - roundsConsumed) : null;

  const spend = readSpend(dash);
  const spentUsd = spend.usedUsd ?? 0;
  const parentBudgetUsd = spend.budgetUsd;
  const spendRemaining =
    parentBudgetUsd != null ? Math.max(0, parentBudgetUsd - spentUsd) : null;

  return {
    roundsConsumed,
    parentMaxRounds,
    roundsRemaining,
    spentUsd,
    parentBudgetUsd,
    spendRemaining,
  };
}

// The `LimitOverrides` a fresh reconcile dialog represents BEFORE the operator
// touches anything — the pre-filled "remaining" values. The steer panel seeds
// its working copy with this so confirming an untouched dialog forks with the
// shown ceilings (not a silent inherit of the parent's full budget). A null
// default (unknown parent ceiling/cap) is omitted = inherit. Matches what
// `LimitReconcile` re-emits from its initial input strings.
export function limitOverridesFromDefaults(d: ForkReconcileDefaults): LimitOverrides {
  const limits: LimitOverrides = {};
  if (d.roundsRemaining != null) limits.max_rounds = d.roundsRemaining;
  if (d.spendRemaining != null) limits.spend_budget_usd = d.spendRemaining;
  return limits;
}
