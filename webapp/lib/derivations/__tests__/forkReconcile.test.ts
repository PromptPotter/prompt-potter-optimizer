import { describe, expect, it } from "vitest";
import { configOverridesFromDefaults, forkReconcileDefaults } from "../forkReconcile";
import type { DashboardSnapshot } from "@/lib/poll";

// "3 of 6 rounds used → 3 left"; "$4 of $10 spent → $6 left". The cap lives in
// run_limits (the authoritative, gate-sourced field); `spend` only carries used.
const dash = {
  rounds: [{ round: 1 }, { round: 2 }, { round: 3 }],
  run_limits: { max_rounds: 6, spend_budget_usd: 10 },
  spend: { total_used_usd: 4 },
} as unknown as DashboardSnapshot;

describe("forkReconcileDefaults", () => {
  it("defaults rounds + spend to the parent's remaining", () => {
    const d = forkReconcileDefaults(dash);
    expect(d.roundsConsumed).toBe(3);
    expect(d.parentMaxRounds).toBe(6);
    expect(d.roundsRemaining).toBe(3);
    expect(d.spentUsd).toBe(4);
    expect(d.parentBudgetUsd).toBe(10);
    expect(d.spendRemaining).toBe(6);
  });

  it("floors rounds-remaining at 1 when the parent is already past its ceiling", () => {
    const over = {
      rounds: [{ round: 1 }, { round: 2 }, { round: 3 }, { round: 4 }, { round: 5 }, { round: 6 }],
      run_limits: { max_rounds: 6 },
    } as unknown as DashboardSnapshot;
    expect(forkReconcileDefaults(over).roundsRemaining).toBe(1);
  });

  it("returns null defaults when the parent ceiling / cap is unknown", () => {
    const bare = { rounds: [{ round: 1 }] } as unknown as DashboardSnapshot;
    const d = forkReconcileDefaults(bare);
    expect(d.roundsConsumed).toBe(1);
    expect(d.parentMaxRounds).toBeNull();
    expect(d.roundsRemaining).toBeNull();
    expect(d.parentBudgetUsd).toBeNull();
    expect(d.spendRemaining).toBeNull();
  });

  it("treats a null dashboard as zero-consumed, all-inherit", () => {
    const d = forkReconcileDefaults(null);
    expect(d.roundsConsumed).toBe(0);
    expect(d.roundsRemaining).toBeNull();
    expect(d.spentUsd).toBe(0);
  });

  it("turns the shown defaults into the pre-confirm ConfigOverrides", () => {
    expect(configOverridesFromDefaults(forkReconcileDefaults(dash))).toEqual({
      max_rounds: 3,
      spend_budget_usd: 6,
    });
    // Unknown parent ceiling/cap → omit (inherit), never a zero/NaN ceiling.
    expect(configOverridesFromDefaults(forkReconcileDefaults(null))).toEqual({});
  });
});
