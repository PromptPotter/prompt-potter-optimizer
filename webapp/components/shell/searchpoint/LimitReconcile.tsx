"use client";

import { useState } from "react";
import type { OptimizerLimit, RunLimitOverrides } from "@/lib/api";
import { forkReconcileDefaults } from "@/lib/derivations";
import { fmtUsd, fmtTokens } from "@/lib/format";
import { parseCap } from "@/lib/run-limits";
import { useCycleStream } from "@/lib/poll";

// The steer flow's run-limit reconcile. A fork numbers its rounds from 1, so rounds + spend default
// to the parent's REMAINING; every other blank field inherits the parent's value.

interface Fields {
  rounds: string;
  spend: string;
  tokens: string;
  // The optimizer's own limits (`run_limits.optimizer`), keyed `node.knob`.
  optimizer: Record<string, string>;
}

const limitKey = (l: OptimizerLimit) => `${l.node}.${l.knob}`;

export function LimitReconcile({
  onChange,
}: {
  onChange: (limits: RunLimitOverrides) => void;
}) {
  const { dash } = useCycleStream();
  // Snapshot once at open: the 2 s poll keeps mutating `dash` and must not clobber typed values.
  const [defaults] = useState(() => forkReconcileDefaults(dash));
  const [rl] = useState(() => dash?.run_limits ?? null);
  // Guarded: `dashboard.json` is served verbatim, and a file an older build wrote lacks it.
  const declared = Array.isArray(rl?.optimizer) ? rl.optimizer : [];
  const [f, setF] = useState<Fields>(() => ({
    rounds: defaults.roundsRemaining != null ? String(defaults.roundsRemaining) : "",
    spend: defaults.spendRemaining != null ? String(defaults.spendRemaining) : "",
    tokens: "",
    optimizer: {},
  }));

  const set = (next: Fields) => {
    setF(next);
    // The floor is 0, not 1: `max_rounds: 0` means "measure the origin and stop".
    const count = { int: true } as const;
    // Each rides the fork's `nodes` overlay under the node that declared it; blank inherits.
    const nodes: Record<string, { config: Record<string, number> }> = {};
    for (const l of declared) {
      const typed = next.optimizer[limitKey(l)];
      if (typed === undefined) continue;
      const value = parseCap(typed, l.integer ? count : { max: 1 });
      if (value == null) continue;
      (nodes[l.node] ??= { config: {} }).config[l.knob] = value;
    }
    const limits: RunLimitOverrides = {
      max_rounds: parseCap(next.rounds, count) ?? undefined,
      spend_budget_usd: parseCap(next.spend) ?? undefined,
      token_budget: parseCap(next.tokens, count) ?? undefined,
      ...(Object.keys(nodes).length ? { nodes } : {}),
    };
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
          {defaults.parentBudgetUsd == null
            ? "parent uncapped — leave blank to inherit"
            : defaults.spentUsd == null
              ? "parent's spend unread — leave blank to inherit its cap"
              : `${fmtUsd(defaults.spentUsd)} of ${fmtUsd(defaults.parentBudgetUsd)} spent — fork starts fresh`}
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
