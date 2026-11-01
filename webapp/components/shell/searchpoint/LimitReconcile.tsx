"use client";

import { useState } from "react";
import type { Budget, OptimizerLimit, RunLimitOverrides } from "@/lib/api";
import { budgetAxis, capLines, readSpend } from "@/lib/derivations";
import { CAP_AXIS_INFO } from "@/lib/api/types.generated";
import { capInput, capText, parseCap, parseCapIn, type CapBounds } from "@/lib/run-limits";
import type { CyclePath } from "@/lib/ids";
import { useDashboardAt } from "@/lib/poll";

interface Fields {
  rounds: string;
  wallClock: string;
  optimizer: Record<string, string>;
}

const limitKey = (l: OptimizerLimit) => `${l.node}.${l.knob}`;
const limitBounds = (l: OptimizerLimit): CapBounds => (l.integer ? { integer: true } : { maximum: 1 });
const ROUNDS = CAP_AXIS_INFO.rounds;
const WALL_CLOCK = CAP_AXIS_INFO.wall_clock_s;

// A refused entry is named on its field, never dropped as if blank: blank inherits the parent's.
const refused = (typed: string | undefined, bounds: CapBounds) =>
  typed !== undefined && "refused" in parseCap(typed, bounds);

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
  const [f, setF] = useState<Fields>({ rounds: "", wallClock: "", optimizer: {} });
  const spend = readSpend(dash);
  const { budgetUsd: usdCap, budgetTokens: tokenCap } = spend;
  const caps = capLines(spend);
  const parentWallClock = rl ? budgetAxis(rl.budget, "wall_clock_s") : null;

  const set = (next: Fields) => {
    setF(next);
    const nodes: Record<string, { config: Record<string, number> }> = {};
    for (const l of declared) {
      const typed = next.optimizer[limitKey(l)];
      if (typed === undefined) continue;
      const cap = parseCap(typed, limitBounds(l));
      if ("value" in cap) (nodes[l.node] ??= { config: {} }).config[l.knob] = cap.value;
    }
    // Blank names nothing, so the axis inherits the parent's.
    const budget: Budget = {};
    const rounds = parseCap(next.rounds, ROUNDS);
    if ("value" in rounds) budget.rounds = rounds.value;
    const wallClock = parseCapIn(next.wallClock, WALL_CLOCK);
    if ("value" in wallClock) budget.wall_clock_s = wallClock.value;
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
        <span className="limit-label">{ROUNDS.title}</span>
        <input
          {...capInput(ROUNDS)}
          className="limit-input"
          value={f.rounds}
          aria-invalid={refused(f.rounds, ROUNDS) || undefined}
          placeholder={ph(left?.rounds, "inherit")}
          aria-label={`Fork ${ROUNDS.title.toLowerCase()}`}
          onChange={(e) => set({ ...f, rounds: e.target.value })}
        />
        <small className="limit-note">
          {left?.parent_rounds != null
            ? `${left.rounds_closed} of ${left.parent_rounds} used — blank runs the rest from R1`
            : "parent uncapped — set a cap for the fork"}
        </small>
      </label>

      <label className="limit-row">
        <span className="limit-label">{WALL_CLOCK.title}</span>
        <input
          {...capInput(WALL_CLOCK)}
          className="limit-input"
          value={f.wallClock}
          aria-invalid={"refused" in parseCapIn(f.wallClock, WALL_CLOCK) || undefined}
          placeholder={parentWallClock != null ? String(parentWallClock / WALL_CLOCK.scale) : "inherit"}
          aria-label={`Fork ${WALL_CLOCK.title.toLowerCase()} in ${WALL_CLOCK.unit}`}
          onChange={(e) => set({ ...f, wallClock: e.target.value })}
        />
        <small className="limit-note">
          {parentWallClock != null
            ? `parent cap ${capText(WALL_CLOCK, parentWallClock)} per launch; blank inherits`
            : `${WALL_CLOCK.unit} per launch; a remote cell still open at the edge is cut — blank inherits`}
        </small>
      </label>

      <div className="limit-row">
        <span className="limit-label">{CAP_AXIS_INFO.usd.title}</span>
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
                  min={0}
                  max={l.integer ? undefined : 1}
                  step={l.integer ? 1 : 0.01}
                  className="limit-input"
                  value={f.optimizer[limitKey(l)] ?? ""}
                  aria-invalid={refused(f.optimizer[limitKey(l)], limitBounds(l)) || undefined}
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
