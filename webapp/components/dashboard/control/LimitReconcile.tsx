"use client";

import { useState } from "react";
import type { ConfigOverrides } from "@/lib/api";
import { forkReconcileDefaults } from "@/lib/derivations";
import { fmtUsd, fmtTokens } from "@/lib/format";
import { useDashboard } from "@/lib/hooks/useDashboard";

// The run-limit reconcile half of the steer flow (decision E). A fork numbers
// its rounds from 1, so the operator confirms the fork's OWN absolute ceilings
// — rounds + spend default to the parent's remaining ("3 of 6 rounds used → 3
// left", "$4 of $10 spent → $6 left"). Patience + elimination epsilon inherit
// the parent by default and live behind an "Advanced" disclosure (placeholders
// show the inherited value; blank = inherit).
//
// Emits a sparse `ConfigOverrides` on every edit: a field is included only when
// the operator's value is present + valid, so blank = inherit. Self-contained
// presentational input; the parent panel folds the result into the OperatorForkOverride.

interface Fields {
  rounds: string;
  spend: string;
  tokens: string;
  l1: string;
  l2: string;
  l3: string;
  eps: string;
}

export function LimitReconcile({
  onChange,
}: {
  onChange: (limits: ConfigOverrides) => void;
}) {
  const { dash } = useDashboard();
  // Snapshot the defaults once at open — the cycle is stopped/paused while
  // steering, but the 2 s poll keeps mutating `dash`; the operator's typed
  // values are the working copy and must not be clobbered by a later tick.
  const [defaults] = useState(() => forkReconcileDefaults(dash));
  const [rl] = useState(() => dash?.run_limits ?? null);
  const [f, setF] = useState<Fields>(() => ({
    rounds: defaults.roundsRemaining != null ? String(defaults.roundsRemaining) : "",
    spend: defaults.spendRemaining != null ? String(defaults.spendRemaining) : "",
    tokens: "",
    l1: "",
    l2: "",
    l3: "",
    eps: "",
  }));

  const set = (next: Fields) => {
    setF(next);
    const limits: ConfigOverrides = {};
    const intGte1 = (s: string) => {
      const n = Number.parseInt(s, 10);
      return s.trim() !== "" && Number.isInteger(n) && n >= 1 ? n : null;
    };
    const r = intGte1(next.rounds);
    if (r != null) limits.max_rounds = r;
    const s = Number.parseFloat(next.spend);
    if (next.spend.trim() !== "" && Number.isFinite(s) && s >= 0) limits.spend_budget_usd = s;
    const tk = Number.parseInt(next.tokens, 10);
    if (next.tokens.trim() !== "" && Number.isInteger(tk) && tk >= 0) limits.token_budget = tk;
    const l1 = intGte1(next.l1);
    if (l1 != null) limits.l1_patience = l1;
    const l2 = intGte1(next.l2);
    if (l2 != null) limits.l2_patience = l2;
    const l3 = intGte1(next.l3);
    if (l3 != null) limits.l3_patience = l3;
    const eps = Number.parseFloat(next.eps);
    if (next.eps.trim() !== "" && Number.isFinite(eps) && eps >= 0 && eps <= 1)
      limits.pobb_epsilon = eps;
    onChange(limits);
  };

  const ph = (v: number | null | undefined, fallback: string) =>
    typeof v === "number" ? String(v) : fallback;

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
          value={f.rounds}
          placeholder="inherit"
          aria-label="Fork max rounds"
          onChange={(e) => set({ ...f, rounds: e.target.value })}
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
            value={f.spend}
            placeholder="no cap"
            aria-label="Fork spend cap in USD"
            onChange={(e) => set({ ...f, spend: e.target.value })}
          />
        </span>
        <small className="limit-note">
          {defaults.parentBudgetUsd != null
            ? `${fmtUsd(defaults.spentUsd)} of ${fmtUsd(defaults.parentBudgetUsd)} spent — fork starts fresh`
            : "parent uncapped — leave blank to inherit"}
        </small>
      </label>

      <label className="limit-row">
        <span className="limit-label">Token cap</span>
        <input
          type="number"
          min={0}
          step={1000}
          inputMode="numeric"
          className="limit-input"
          value={f.tokens}
          placeholder={ph(rl?.token_budget, "inherit")}
          aria-label="Fork token cap"
          onChange={(e) => set({ ...f, tokens: e.target.value })}
        />
        <small className="limit-note">
          {typeof rl?.token_budget === "number"
            ? `parent cap ${fmtTokens(rl.token_budget)} — blank inherits`
            : "parent uncapped — leave blank to inherit"}
        </small>
      </label>

      <details className="limit-advanced">
        <summary>Advanced — patience &amp; elimination</summary>
        <div className="limit-advanced-body">
          <label className="limit-row">
            <span className="limit-label">L1 patience</span>
            <input
              type="number"
              min={1}
              step={1}
              className="limit-input"
              value={f.l1}
              placeholder={ph(rl?.l1_patience, "inherit")}
              aria-label="Fork L1 patience"
              onChange={(e) => set({ ...f, l1: e.target.value })}
            />
          </label>
          <label className="limit-row">
            <span className="limit-label">L2 patience</span>
            <input
              type="number"
              min={1}
              step={1}
              className="limit-input"
              value={f.l2}
              placeholder={ph(rl?.l2_patience, "inherit")}
              aria-label="Fork L2 patience"
              onChange={(e) => set({ ...f, l2: e.target.value })}
            />
          </label>
          <label className="limit-row">
            <span className="limit-label">L3 patience</span>
            <input
              type="number"
              min={1}
              step={1}
              className="limit-input"
              value={f.l3}
              placeholder={ph(rl?.l3_patience, "inherit")}
              aria-label="Fork L3 patience"
              onChange={(e) => set({ ...f, l3: e.target.value })}
            />
          </label>
          <label className="limit-row">
            <span className="limit-label">PoBB ε</span>
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              className="limit-input"
              value={f.eps}
              placeholder={ph(rl?.pobb_epsilon, "inherit")}
              aria-label="Fork PoBB elimination epsilon"
              onChange={(e) => set({ ...f, eps: e.target.value })}
            />
          </label>
          <small className="limit-note">Blank inherits the parent&apos;s value.</small>
        </div>
      </details>

      <small className="limit-reconcile-foot">The fork numbers rounds from 1.</small>
    </div>
  );
}
