"use client";

import { useState } from "react";
import type { LimitOverrides } from "@/lib/api";
import { forkReconcileDefaults } from "@/lib/derivations/forkReconcile";
import { fmtUsd } from "@/lib/format";
import type { DashboardSnapshot } from "@/lib/poll";

// The run-limit reconcile half of the steer flow (decision E). A fork numbers
// its rounds from 1, so the operator confirms the fork's OWN absolute ceilings
// — defaulted to the parent's remaining ("3 of 6 rounds used → 3 left",
// "$4 of $10 spent → $6 left"). Rounds + spend are the two knobs surfaced;
// patience/epsilon inherit the parent (LimitOverrides leaves them absent).
//
// Emits a sparse `LimitOverrides` on every edit: a field is included only when
// the operator's value is present + valid, so blank = inherit. Self-contained
// presentational input; the parent panel folds the result into the ForkSeed.
export function LimitReconcile({
  dash,
  onChange,
}: {
  dash: DashboardSnapshot | null;
  onChange: (limits: LimitOverrides) => void;
}) {
  // Snapshot the defaults once at open — the cycle is stopped/paused while
  // steering, but the 2 s poll keeps mutating `dash`; the operator's typed
  // values are the working copy and must not be clobbered by a later tick.
  const [defaults] = useState(() => forkReconcileDefaults(dash));
  const [roundsStr, setRoundsStr] = useState<string>(
    defaults.roundsRemaining != null ? String(defaults.roundsRemaining) : "",
  );
  const [spendStr, setSpendStr] = useState<string>(
    defaults.spendRemaining != null ? String(defaults.spendRemaining) : "",
  );

  const emit = (rounds: string, spend: string) => {
    const limits: LimitOverrides = {};
    const r = Number.parseInt(rounds, 10);
    if (rounds.trim() !== "" && Number.isInteger(r) && r >= 1) limits.max_rounds = r;
    const s = Number.parseFloat(spend);
    if (spend.trim() !== "" && Number.isFinite(s) && s >= 0) limits.spend_budget_usd = s;
    onChange(limits);
  };

  const onRounds = (v: string) => {
    setRoundsStr(v);
    emit(v, spendStr);
  };
  const onSpend = (v: string) => {
    setSpendStr(v);
    emit(roundsStr, v);
  };

  return (
    <div className="limit-reconcile">
      <span className="limit-reconcile-title">Reconcile run limits</span>

      <label className="limit-row">
        <span className="limit-label">Rounds</span>
        <input
          type="number"
          min={1}
          step={1}
          inputMode="numeric"
          className="limit-input"
          value={roundsStr}
          placeholder="inherit"
          aria-label="Fork max rounds"
          onChange={(e) => onRounds(e.target.value)}
        />
        <small className="limit-note">
          {defaults.parentMaxRounds != null
            ? `${defaults.roundsConsumed} of ${defaults.parentMaxRounds} used — fork runs this many from R1`
            : "parent uncapped — set a ceiling for the fork"}
        </small>
      </label>

      <label className="limit-row">
        <span className="limit-label">Spend cap</span>
        <span className="limit-input-usd">
          <span className="limit-usd-prefix">$</span>
          <input
            type="number"
            min={0}
            step={0.5}
            inputMode="decimal"
            className="limit-input"
            value={spendStr}
            placeholder="no cap"
            aria-label="Fork spend cap in USD"
            onChange={(e) => onSpend(e.target.value)}
          />
        </span>
        <small className="limit-note">
          {defaults.parentBudgetUsd != null
            ? `${fmtUsd(defaults.spentUsd)} of ${fmtUsd(defaults.parentBudgetUsd)} spent — fork starts fresh`
            : "parent uncapped — leave blank to inherit"}
        </small>
      </label>

      <small className="limit-reconcile-foot">
        Patience + elimination thresholds inherit the parent. The fork numbers
        rounds from 1.
      </small>
    </div>
  );
}
