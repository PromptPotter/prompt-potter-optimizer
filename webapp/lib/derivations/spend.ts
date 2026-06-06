// Single parser for the dashboard `spend` block. The block is written by
// LiveDashboardProjection from per-sample step_tokens (backend bucket) +
// ledger TokenUsageRecord (loop bucket); OpenRouter ships USD on the wire,
// other providers resolve through shared/spend.py's rate table.
//
// ChatPane (efficiency + ETA chips) consumes this. The *extraction* — the
// bucket defaults, the `used_usd` type-guards, the total/fallback rule — lives
// here once so every consuming surface agrees on the underlying numbers.

import type { DashboardSnapshot } from "@/lib/poll";

interface SpendBucket {
  used_usd?: number;
  input_tokens?: number;
  output_tokens?: number;
  rate_known?: boolean;
}

export interface SpendView {
  backendUsd: number;
  loopUsd: number;
  // `total_used_usd` when present, else backend + loop.
  totalUsd: number;
  // totalUsd when > 0, else null — "no spend yet" vs "$0.00".
  usedUsd: number | null;
  // The two armed ceilings, read from the authoritative `run_limits` block
  // (the gate's source). `null` = that ceiling is disarmed.
  budgetUsd: number | null;
  budgetTokens: number | null;
  // At least one bucket reported a USD rate; when false, USD is unreliable
  // and the caller should fall back to a token count.
  rateKnown: boolean;
  // Per-bucket input+output token sums (the no-rate fallback display), and
  // their total (the token ceiling's spent side).
  backendTokens: number;
  loopTokens: number;
  totalTokens: number;
}

export function readSpend(dash: DashboardSnapshot | null): SpendView {
  const block = (dash as Record<string, unknown> | null)?.spend as
    | {
        backend?: SpendBucket;
        loop?: SpendBucket;
        total_used_usd?: number;
      }
    | undefined;
  const backend = block?.backend ?? {};
  const loop = block?.loop ?? {};
  const backendUsd = typeof backend.used_usd === "number" ? backend.used_usd : 0;
  const loopUsd = typeof loop.used_usd === "number" ? loop.used_usd : 0;
  const totalUsd =
    typeof block?.total_used_usd === "number" ? block.total_used_usd : backendUsd + loopUsd;
  const backendTokens = (backend.input_tokens ?? 0) + (backend.output_tokens ?? 0);
  const loopTokens = (loop.input_tokens ?? 0) + (loop.output_tokens ?? 0);
  // The caps live in `run_limits` (written at INIT + re-emitted by forks), not
  // in the `spend` rollup — `spend` only carries what's been *used*.
  const limits = dash?.run_limits;
  return {
    backendUsd,
    loopUsd,
    totalUsd,
    usedUsd: totalUsd > 0 ? totalUsd : null,
    budgetUsd: typeof limits?.spend_budget_usd === "number" ? limits.spend_budget_usd : null,
    budgetTokens: typeof limits?.token_budget === "number" ? limits.token_budget : null,
    rateKnown: !!(backend.rate_known || loop.rate_known),
    backendTokens,
    loopTokens,
    totalTokens: backendTokens + loopTokens,
  };
}
