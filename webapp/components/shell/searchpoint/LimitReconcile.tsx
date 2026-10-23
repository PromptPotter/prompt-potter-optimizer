"use client";

import { useState } from "react";
import type { Budget, OptimizerLimit, RunLimitOverrides } from "@/lib/api";
import { budgetAxis, capLines, readSpend } from "@/lib/derivations";
import { fmtDuration } from "@/lib/format";
import { parseCap } from "@/lib/run-limits";
import type { CyclePath } from "@/lib/ids";
import { useDashboardAt } from "@/lib/poll";

interface Fields {
  rounds: string;
  wallClockMin: string;
  optimizer: Record<string, string>;
}

const limitKey = (l: OptimizerLimit) => `${l.node}.${l.knob}`;

export function LimitReconcile({
  path,
  onChange,
}: {
  path: CyclePath;
  onChange: (limits: RunLimitOverrides) => void;
}) {
  const dash = useDashboardAt(path);
  const left = dash?.fork_remainder ?? null;
  const rl = dash?.run_limits ?? null;
  const declared = rl?.optimizer ?? [];
  const [f, setF] = useState<Fields>({ rounds: "", wallClockMin: "", optimizer: {} });
  const spend = readSpend(dash);
  const { budgetUsd: usdCap, budgetTokens: tokenCap } = spend;
  const caps = capLines(spend);
  const parentWallClock = rl ? budgetAxis(rl.budget, "wall_clock_s") : null;

  const set = (next: Fields) => {
    setF(next);
    // The floor is 0, not 1: `budget.rounds: 0` means "measure the origin and stop".
    const count = { int: true } as const;
    const nodes: Record<string, { config: Record<string, number> }> = {};
    for (const l of declared) {
      const typed = next.optimizer[limitKey(l)];
      if (typed === undefined) continue;
      const value = parseCap(typed, l.integer ? count : { max: 1 });
      if (value == null) continue;
      (nodes[l.node] ??= { config: {} }).config[l.knob] = value;
    }
    // Blank names nothing, so the axis inherits the parent's.
    const budget: Budget = {};
    const rounds = parseCap(next.rounds, count);
    if (rounds != null) budget.rounds = rounds;
    const minutes = parseCap(next.wallClockMin, { min: Number.MIN_VALUE });
    if (minutes != null) budget.wall_clock_s = minutes * 60;
    onChange({
      ...(Object.keys(budget).length ? { budget } : {}),
      ...(Object.keys(nodes).length ? { nodes } : {}),
    });
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
          placeholder={ph(left?.rounds, "inherit")}
          aria-label="Fork round cap"
          onChange={(e) => set({ ...f, rounds: e.target.value })}
        />
        <small className="limit-note">
          {left?.parent_rounds != null
            ? `${left.rounds_closed} of ${left.parent_rounds} used — blank runs the rest from R1`
            : "parent uncapped — set a cap for the fork"}
        </small>
      </label>

      <label className="limit-row">
        <span className="limit-label">Wall-clock cap</span>
        <input
          type="number"
          min={0}
          step={5}
          inputMode="decimal"
          className="limit-input"
          value={f.wallClockMin}
          placeholder={parentWallClock != null ? String(parentWallClock / 60) : "inherit"}
          aria-label="Fork wall-clock cap in minutes"
          onChange={(e) => set({ ...f, wallClockMin: e.target.value })}
        />
        <small className="limit-note">
          {parentWallClock != null
            ? `parent cap ${fmtDuration(parentWallClock)} per launch — minutes; blank inherits`
            : "minutes per launch; a remote cell still open at the edge is cut — blank inherits"}
        </small>
      </label>

      <div className="limit-row">
        <span className="limit-label">Spend cap</span>
        <small className="limit-note">
          {usdCap == null && tokenCap == null
            ? "the campaign is uncapped — set its cap on the run's controls"
            : `${caps.length ? caps.join(" · ") : "the campaign's cap"} — one cap the fork shares with every live cycle, never a remainder`}
        </small>
      </div>

      {declared.length > 0 ? (
        <details className="limit-advanced">
          <summary>Advanced — the optimizer&apos;s own limits</summary>
          <div className="limit-advanced-body">
            {declared.map((l) => (
              <label key={limitKey(l)} className="limit-row">
                <span className="limit-label">{l.label}</span>
                <input
                  type="number"
                  min={l.integer ? 1 : 0}
                  max={l.integer ? undefined : 1}
                  step={l.integer ? 1 : 0.01}
                  className="limit-input"
                  value={f.optimizer[limitKey(l)] ?? ""}
                  placeholder={ph(l.value, "inherit")}
                  aria-label={`Fork ${l.label}`}
                  onChange={(e) =>
                    set({ ...f, optimizer: { ...f.optimizer, [limitKey(l)]: e.target.value } })
                  }
                />
              </label>
            ))}
            <small className="limit-note">Blank inherits the parent&apos;s value.</small>
          </div>
        </details>
      ) : (
        <small className="limit-note">
          This optimizer declares no run limits of its own; its knobs are in the Optimizer card.
        </small>
      )}

      <small className="limit-reconcile-foot">The fork numbers rounds from 1.</small>
    </div>
  );
}
