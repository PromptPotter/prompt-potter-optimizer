// Single parser for the dashboard `spend` block. The block is written by
// LiveDashboardProjection from per-sample step_tokens (backend bucket) +
// ledger TokenUsageRecord (loop bucket); OpenRouter ships USD on the wire,
// other providers resolve through shared/spend.py's rate table.
//
// ChatPane (efficiency + ETA chips) consumes this. The *extraction* — the
// bucket defaults, the `used_usd` type-guards, the total/fallback rule — lives
// here once so every consuming surface agrees on the underlying numbers.

import type { DashboardSnapshot } from "@/lib/poll";

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
  // Tokens billed with no resolvable USD cost (e.g. Groq returns no wire cost
  // and the model isn't in the rate table). When >0 the USD spend is undercounted.
  unpricedTokens: number;
  // A USD budget is armed but real unpriced spend exists, so the cap is blind to
  // it — the surface shows a loud "USD cap inactive" warning; token cap backstops.
  capInactive: boolean;
}

export function readSpend(dash: DashboardSnapshot | null): SpendView {
  // `spend` is firm once present (SpendRollup); only the block itself is
  // optional, absent on a null/warming-up snapshot. Each bucket + its fields
  // are guaranteed by the Python model, so they're read directly.
  const block = dash?.spend;
  const backend = block?.backend;
  const loop = block?.loop;
  const backendUsd = backend?.used_usd ?? 0;
  const loopUsd = loop?.used_usd ?? 0;
  const totalUsd = block?.total_used_usd ?? 0;
  const backendTokens = backend ? backend.input_tokens + backend.output_tokens : 0;
  const loopTokens = loop ? loop.input_tokens + loop.output_tokens : 0;
  const unpricedTokens = (backend?.unpriced_tokens ?? 0) + (loop?.unpriced_tokens ?? 0);
  // The caps live in `run_limits` (written at INIT + re-emitted by forks), not
  // in the `spend` rollup — `spend` only carries what's been *used*.
  const limits = dash?.run_limits;
  const budgetUsd = typeof limits?.spend_budget_usd === "number" ? limits.spend_budget_usd : null;
  return {
    backendUsd,
    loopUsd,
    totalUsd,
    usedUsd: totalUsd > 0 ? totalUsd : null,
    budgetUsd,
    budgetTokens: typeof limits?.token_budget === "number" ? limits.token_budget : null,
    rateKnown: Boolean(backend?.rate_known || loop?.rate_known),
    backendTokens,
    loopTokens,
    totalTokens: backendTokens + loopTokens,
    unpricedTokens,
    capInactive: budgetUsd != null && unpricedTokens > 0,
  };
}
