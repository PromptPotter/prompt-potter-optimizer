// Pure derivations over the `optimizer_locks` block the draft wire carries.
// The new-campaign permission step renders from these — what the optimizer may
// and may not permute on the backend pipeline, surfaced *before* commit.
//
// Data→data only (no React, no I/O) so it rides the lib Vitest scope.

import type { OptimizerLocks } from "@/lib/api";

// The operator-facing "thinking" ladder. The backend's internal off-states
// (`none` / `default`) are not shown as choices — the operator reasons about
// low / medium / high; anything not in the node's allowed set renders
// crossed-out (the optimizer is locked out of escalating to it).
export const THINKING_LADDER = ["low", "medium", "high"] as const;
export type ThinkingLevel = (typeof THINKING_LADDER)[number];

export interface ThinkingOption {
  key: ThinkingLevel;
  active: boolean; // the node's currently-configured reasoning_effort
  allowed: boolean; // the optimizer may permute to this level
}

export interface ThinkingControl {
  value: string | null;
  options: ThinkingOption[];
}

// The node the UI fronts — the first pipeline step that has a locks entry,
// else the first pipeline step, else the first node key (defensive).
export function primaryNode(locks: OptimizerLocks): string | null {
  for (const step of locks.pipeline) {
    if (locks.nodes[step]) return step;
  }
  return locks.pipeline[0] ?? Object.keys(locks.nodes)[0] ?? null;
}

// Derive the thinking control for the primary backend node. `value` is the
// active reasoning_effort; each ladder option carries whether it's active and
// whether the optimizer is allowed to permute to it (membership in the node's
// `param_allowed_values.reasoning_effort`).
export function thinkingLadder(
  locks: OptimizerLocks,
  node: string | null = primaryNode(locks),
): ThinkingControl {
  const n = node ? locks.nodes[node] : undefined;
  const raw = n?.config?.reasoning_effort;
  const value = typeof raw === "string" ? raw : null;
  const allowed = new Set(n?.param_allowed_values?.reasoning_effort ?? []);
  return {
    value,
    options: THINKING_LADDER.map((key) => ({
      key,
      active: key === value,
      allowed: allowed.has(key),
    })),
  };
}

export type LockReason = "forbidden" | "constrained" | "free";

export interface ParamLock {
  node: string;
  param: string;
  value: unknown; // undefined for a forbidden axis absent from config
  locked: boolean;
  reason: LockReason;
}

// Flatten every node's params into a locked/free list for the "Review
// hyperparameters" expander. A param is:
//   * `forbidden` — model/provider, pinned campaign-wide (locked)
//   * `constrained` — has a `param_allowed_values` entry, so the optimizer is
//     restricted to a subset (locked; e.g. reasoning_effort)
//   * `free` — no restriction; the optimizer may move it (e.g. temperature)
// Forbidden axes absent from a node's config still appear (value `undefined`)
// so the lock is visible.
export function lockedParams(locks: OptimizerLocks): ParamLock[] {
  const forbidden = new Set(locks.forbidden_axes);
  const out: ParamLock[] = [];
  for (const [node, n] of Object.entries(locks.nodes)) {
    const seen = new Set<string>();
    for (const [param, value] of Object.entries(n.config)) {
      seen.add(param);
      out.push(classify(node, param, value, forbidden, n.param_allowed_values));
    }
    for (const axis of locks.forbidden_axes) {
      if (!seen.has(axis)) {
        out.push({ node, param: axis, value: undefined, locked: true, reason: "forbidden" });
      }
    }
  }
  return out;
}

function classify(
  node: string,
  param: string,
  value: unknown,
  forbidden: Set<string>,
  allowedValues: Record<string, string[]>,
): ParamLock {
  if (forbidden.has(param)) {
    return { node, param, value, locked: true, reason: "forbidden" };
  }
  if (param in allowedValues) {
    return { node, param, value, locked: true, reason: "constrained" };
  }
  return { node, param, value, locked: false, reason: "free" };
}
