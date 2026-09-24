// The single parser for the dashboard `spend` block. A bucket may be ABSENT from the served file
// (`diagnostic` usually is), so index a rollup through a guard, never an annotation.

import type { SpendBucket, SpendRollup } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { cacheShare, prefixReading, type PrefixReading } from "./token-account";

// Display order, biggest first. Must stay total over `domain/spend.py::TOKEN_KIND_BUCKET`: an
// unlisted server bucket renders as an unexplained gap between the rows and the served total.
export const SPEND_BUCKETS = [
  { key: "backend", label: "Backend" },
  { key: "loop", label: "Loop" },
  { key: "judge", label: "Judge" },
  { key: "diagnostic", label: "Diagnostic" },
] as const satisfies readonly { key: keyof SpendRollup; label: string }[];

export interface SpendView {
  backendUsd: number;
  loopUsd: number;
  judgeUsd: number;
  // Served, never summed here. 0 where the block is absent — `usedUsd` separates that from $0.00.
  totalUsd: number;
  usedUsd: number | null;
  // From `run_limits`, the gate's source; `null` = that ceiling is disarmed.
  budgetUsd: number | null;
  budgetTokens: number | null;
  // False ⇒ USD is unreliable; fall back to a token count.
  rateKnown: boolean;
  // No `SpendBucket.total_tokens` on the wire: `extra="forbid"` on disk refuses a `@computed_field`.
  backendTokens: number;
  loopTokens: number;
  judgeTokens: number;
  // Served: the same number the halt probe reads (`SpendRollup.total_tokens_used`).
  totalTokens: number;
  // >0 ⇒ `totalUsd` is a floor the USD cap cannot see past.
  unpricedTokens: number;
  // A subset of output tokens: explains wall-clock, not money.
  loopReasoningShare: number | null;
  // Provider prefix-cache share, PER BUCKET, never pooled: the backend's volume would swamp the rest.
  // A different fact from a 📖 replayed sample, which reached no provider at all.
  backendCacheShare: number | null;
  loopCacheShare: number | null;
  judgeCacheShare: number | null;
  // Writes with no reads is paying to fill a prefix nothing collects.
  backendCacheWrite: number;
  loopCacheWrite: number;
  judgeCacheWrite: number;
}

export function readSpend(dash: DashboardSnapshot | null): SpendView {
  const block = dash?.spend;
  const backend = block?.backend;
  const loop = block?.loop;
  const judge = block?.judge;
  const buckets = SPEND_BUCKETS.map((b) => block?.[b.key]).filter((b) => b != null);
  const backendUsd = backend?.used_usd ?? 0;
  const loopUsd = loop?.used_usd ?? 0;
  const judgeUsd = judge?.used_usd ?? 0;
  const totalUsd = block?.total_used_usd ?? 0;
  const backendTokens = backend ? backend.input_tokens + backend.output_tokens : 0;
  const loopTokens = loop ? loop.input_tokens + loop.output_tokens : 0;
  const judgeTokens = judge ? judge.input_tokens + judge.output_tokens : 0;
  // `replayed: false` by construction: `_handle_token_usage` folds only uncached records into a bucket.
  const shareOf = (b: SpendBucket | undefined): number | null =>
    cacheShare(b?.cache_read_tokens, b?.input_tokens, false);
  const limits = dash?.run_limits;
  const budgetUsd = typeof limits?.spend_budget_usd === "number" ? limits.spend_budget_usd : null;
  return {
    backendUsd,
    loopUsd,
    judgeUsd,
    totalUsd,
    usedUsd: totalUsd > 0 ? totalUsd : null,
    budgetUsd,
    budgetTokens: typeof limits?.token_budget === "number" ? limits.token_budget : null,
    rateKnown: buckets.some((b) => b.rate_known),
    backendTokens,
    loopTokens,
    judgeTokens,
    totalTokens: block?.total_tokens_used ?? 0,
    unpricedTokens: block?.unpriced_tokens ?? 0,
    loopReasoningShare:
      loop && loop.output_tokens > 0 ? loop.reasoning_tokens / loop.output_tokens : null,
    backendCacheShare: shareOf(backend),
    loopCacheShare: shareOf(loop),
    judgeCacheShare: shareOf(judge),
    backendCacheWrite: backend?.cache_write_tokens ?? 0,
    loopCacheWrite: loop?.cache_write_tokens ?? 0,
    judgeCacheWrite: judge?.cache_write_tokens ?? 0,
  };
}

export interface RoundCostBucket {
  key: string;
  label: string;
  usd: number;
  prefix: PrefixReading;
  write: number;
}

export interface RoundCost {
  round: number;
  totalUsd: number;
  buckets: RoundCostBucket[];
}

/** A bucket the round never carried is dropped, not zeroed: that arithmetic is the server's. */
export function roundCosts(dash: DashboardSnapshot | null): RoundCost[] {
  const by = dash?.spend_by_round;
  if (!by) return [];
  const out: RoundCost[] = [];
  for (const [key, rollup] of Object.entries(by)) {
    const round = Number(key);
    if (!Number.isInteger(round) || !rollup) continue;
    out.push({
      round,
      totalUsd: rollup.total_used_usd,
      buckets: SPEND_BUCKETS.flatMap(({ key: k, label }) => {
        const b: SpendBucket | undefined = rollup[k];
        return b == null
          ? []
          : [
              {
                key: k,
                label,
                usd: b.used_usd,
                prefix: prefixReading(cacheShare(b.cache_read_tokens, b.input_tokens, false), false),
                write: b.cache_write_tokens,
              },
            ];
      }),
    });
  }
  return out.sort((a, b) => a.round - b.round);
}
