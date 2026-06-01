"use client";

import type { ParamLock } from "@/lib/optimizer-locks";

// The full per-node lock list shown under "Review hyperparameters" — every
// param tagged locked (model/provider; constrained allow-sets) vs optimizer-free.
export function LockTable({ params }: { params: ParamLock[] }) {
  if (params.length === 0) return null;
  const REASON_LABEL: Record<ParamLock["reason"], string> = {
    forbidden: "optimizer can't change this",
    constrained: "limited to an allowed set",
    free: "optimizer-free",
  };
  return (
    <ul className="opt-locks-table">
      {params.map((p) => (
        <li key={`${p.node}.${p.param}`} className="opt-locks-param">
          <span className="opt-locks-param-name">
            {p.node}.{p.param}
          </span>
          {p.value !== undefined ? (
            <code className="opt-locks-param-value">{String(p.value)}</code>
          ) : (
            <span className="opt-locks-param-value opt-locks-param-value--empty">—</span>
          )}
          <span
            className={`opt-locks-tag${p.locked ? " is-locked" : " is-free"}`}
            title={REASON_LABEL[p.reason]}
          >
            {p.locked ? "🔒 locked" : "✎ free"}
          </span>
        </li>
      ))}
    </ul>
  );
}
