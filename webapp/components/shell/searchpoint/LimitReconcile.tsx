"use client";

import { useState } from "react";
import type { OptimizerLimit, RunLimitOverrides } from "@/lib/api";
import { fmtUsd, fmtTokens } from "@/lib/format";
import { parseCap } from "@/lib/run-limits";
import type { CyclePath } from "@/lib/ids";
import { useDashboardAt } from "@/lib/poll";

interface Fields {
  rounds: string;
  spend: string;
  tokens: string;
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
  const [f, setF] = useState<Fields>({ rounds: "", spend: "", tokens: "", optimizer: {} });

  const set = (next: Fields) => {
    setF(next);
    // The floor is 0, not 1: `max_rounds: 0` means "measure the origin and stop".
    const count = { int: true } as const;
    const nodes: Record<string, { config: Record<string, number> }> = {};
    for (const l of declared) {
      const typed = next.optimizer[limitKey(l)];
      if (typed === undefined) continue;
      const value = parseCap(typed, l.integer ? count : { max: 1 });
      if (value == null) continue;
      (nodes[l.node] ??= { config: {} }).config[l.knob] = value;
    }
    const usd = parseCap(next.spend) ?? null;
    const tokens = parseCap(next.tokens, count) ?? null;
    const limits: RunLimitOverrides = {
      max_rounds: parseCap(next.rounds, count) ?? undefined,
      ...(usd === null && tokens === null ? {} : { ceiling: { usd, tokens } }),
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
          placeholder={ph(left?.max_rounds, "inherit")}
          aria-label="Fork max rounds"
          onChange={(e) => set({ ...f, rounds: e.target.value })}
        />
        <small className="limit-note">
          {left?.parent_max_rounds != null
            ? `${left.rounds_closed} of ${left.parent_max_rounds} used — blank runs the rest from R1`
            : "parent uncapped — set a cap for the fork"}
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
            placeholder={ph(left?.ceiling.usd, "no cap")}
            aria-label="Fork spend cap in USD"
            onChange={(e) => set({ ...f, spend: e.target.value })}
          />
        </span>
        <small className="limit-note">
          {left?.parent_ceiling.usd == null
            ? "parent uncapped — leave blank to inherit"
            : left.metered_usd == null
              ? "parent's spend unread — leave blank to inherit its cap"
              : `${fmtUsd(left.metered_usd)} of ${fmtUsd(left.parent_ceiling.usd)} counted — blank caps the fork at the rest`}
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
          placeholder={ph(left?.parent_ceiling.tokens, "inherit")}
          aria-label="Fork token cap"
          onChange={(e) => set({ ...f, tokens: e.target.value })}
        />
        <small className="limit-note">
          {typeof left?.parent_ceiling.tokens === "number"
            ? `parent cap ${fmtTokens(left.parent_ceiling.tokens)} — blank inherits`
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
