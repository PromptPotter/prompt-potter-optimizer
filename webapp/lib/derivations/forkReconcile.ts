// A fork's limits default to the PARENT'S REMAINING rounds and spend, off the polled
// `dashboard.json` only. A fork numbers from 1, so remaining IS its `max_rounds`, floored at 1.

import type { RunLimitOverrides } from "@/lib/api";
import type { DashboardSnapshot } from "@/lib/poll";
import { readSpend } from "./spend";

export interface ForkReconcileDefaults {
  roundsConsumed: number;
  // null ⇒ uncapped or unknown, and the rounds input starts blank (inherit).
  parentMaxRounds: number | null;
  roundsRemaining: number | null;
  spentUsd: number;
  parentBudgetUsd: number | null;
  // null ⇒ the parent is uncapped, and so is the fork.
  spendRemaining: number | null;
}

export function forkReconcileDefaults(dash: DashboardSnapshot | null): ForkReconcileDefaults {
  // `rounds[]` carries the ORIGIN at index 0 while `max_rounds` counts L1 rounds. A rewind clamps
  // `rounds[]` below what the parent really spent, so this under-reports there.
  const roundsConsumed = Array.isArray(dash?.rounds)
    ? dash.rounds.filter((r) => r.round > 0).length
    : 0;

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

// An untouched dialog forks with the shown ceilings, never a silent inherit; a null default is
// omitted (= inherit). Must match what `LimitReconcile` emits from its initial inputs.
export function configOverridesFromDefaults(d: ForkReconcileDefaults): RunLimitOverrides {
  const limits: RunLimitOverrides = {};
  if (d.roundsRemaining != null) limits.max_rounds = d.roundsRemaining;
  if (d.spendRemaining != null) limits.spend_budget_usd = d.spendRemaining;
  return limits;
}
