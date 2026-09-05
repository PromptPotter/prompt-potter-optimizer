// Single parser for the dashboard `spend` block. The block is written by
// LiveDashboardProjection from per-sample step_tokens (backend bucket) +
// ledger TokenUsageRecord (loop and judge buckets); OpenRouter ships USD on
// the wire, other providers resolve through shared/pricing.py's rate table.
//
// ChatPane (efficiency + ETA chips) consumes this. The *extraction* — the
// bucket defaults, the `used_usd` type-guards, the total/fallback rule — lives
// here once so every consuming surface agrees on the underlying numbers.

import type { SpendBucket, SpendRollup } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { cacheShare, prefixReading, type PrefixReading } from "./token-account";

// The three buckets and the display word for each. ONE list: naming a subset by hand is what left
// `judge` — grading, and the bucket with the most cacheable prefix — out of `rateKnown` and out of
// this view entirely.
//
// DISPLAY order, biggest first, which is not `domain/spend.py::TOKEN_KIND_BUCKET`'s order and is
// not trying to be: that one is a mapping walked for TOTALITY (a new bucket cannot be dropped from
// a fold), this one is a reading order (backend is ~95% of a campaign's spend, so leading with the
// optimizer's fraction of a cent buries the number). Both must stay total over the same three.
export const SPEND_BUCKETS = [
  { key: "backend", label: "Backend" },
  { key: "loop", label: "Loop" },
  { key: "judge", label: "Judge" },
] as const satisfies readonly { key: keyof SpendRollup; label: string }[];

export interface SpendView {
  backendUsd: number;
  loopUsd: number;
  // Grading's own LLM spend. A third bucket, not a flavour of the other two — folded into `loop`
  // an operator reads grading cost as optimizer cost (`domain/spend.py::TokenUsageKind`).
  judgeUsd: number;
  // SERVED (`SpendRollup.total_used_usd`), never a sum made here: the rollup folds over
  // `spend.buckets`, so a fourth bucket reaches this number without anyone editing this file.
  // 0 where the block is absent, which `usedUsd` below is what separates from a measured zero.
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
  // Per-bucket input+output token sums — the no-rate fallback display, and the only
  // sums still made here, because no bucket serves its own.
  backendTokens: number;
  loopTokens: number;
  judgeTokens: number;
  // The token ceiling's spent side, SERVED. It is the same number the halt probe reads
  // (`SpendRollup.total_tokens_used`); summing the buckets here made the gauge and the
  // gate two different computations in two languages.
  totalTokens: number;
  // >0 ⇒ some calls had no resolvable USD rate, so `totalUsd` is a floor and the USD cap
  // cannot see the difference. Served, for the same reason.
  unpricedTokens: number;
  // Fraction of the LOOP bucket's output tokens the optimizer spent thinking rather
  // than answering; null when nothing has been billed yet or the models report no
  // breakdown. A subset of the output tokens, never an addition to them — it explains
  // where the wall-clock went, not where the money did.
  loopReasoningShare: number | null;
  // Fraction of each bucket's input tokens the PROVIDER served off its own prompt-prefix cache —
  // the discount on calls that did reach a provider. A different fact from a sample marked 📖,
  // which reached none at all. null where that bucket has billed no input.
  //
  // PER BUCKET, and never summed into one headline: the three run on different prompts against
  // different providers, and a backend row carries ~86k input against a judge's ~1.6k, so one
  // ratio over the pooled counts is the backend's share wearing everyone's name. Measured live
  // while this was a single number — backend 20.0%, judge 42.1%, optimizer 0.0% — the pooled
  // reading rounded the judge away, which is precisely the bucket whose rubric is a module
  // constant and whose prefix pays best.
  backendCacheShare: number | null;
  loopCacheShare: number | null;
  judgeCacheShare: number | null;
  // Input tokens billed at a PREMIUM to populate that same prefix cache. Beside the share above
  // because the pair is the whole economics: a write is what makes the next read cheap, so writes
  // with no reads is paying to fill a prefix nothing ever collects
  // (`domain/run_records.py::cache_write_tokens`). Served all along and read by nothing, which is
  // why a bucket could sit at 0% capture for a whole campaign with no line saying it was odd.
  backendCacheWrite: number;
  loopCacheWrite: number;
  judgeCacheWrite: number;
}

export function readSpend(dash: DashboardSnapshot | null): SpendView {
  // `spend` is firm once present (SpendRollup); only the block itself is
  // optional, absent on a null/warming-up snapshot. Each bucket + its fields
  // are guaranteed by the Python model, so they're read directly.
  const block = dash?.spend;
  const backend = block?.backend;
  const loop = block?.loop;
  const judge = block?.judge;
  // Every bucket, from the one declaration above — the browser's `SpendRollup.buckets`.
  const buckets = SPEND_BUCKETS.map((b) => block?.[b.key]).filter((b) => b != null);
  const backendUsd = backend?.used_usd ?? 0;
  const loopUsd = loop?.used_usd ?? 0;
  const judgeUsd = judge?.used_usd ?? 0;
  const totalUsd = block?.total_used_usd ?? 0;
  const backendTokens = backend ? backend.input_tokens + backend.output_tokens : 0;
  const loopTokens = loop ? loop.input_tokens + loop.output_tokens : 0;
  const judgeTokens = judge ? judge.input_tokens + judge.output_tokens : 0;
  // `replayed: false` is a statement about the BUCKET, not a shortcut: `_handle_token_usage`
  // adds to `input_tokens` / `cache_read_tokens` only when `not record.cached`, so a bucket holds
  // billed calls alone and there is no replay in it to misreport a discount for.
  const shareOf = (b: SpendBucket | undefined): number | null =>
    cacheShare(b?.cache_read_tokens, b?.input_tokens, false);
  // The caps live in `run_limits` (written at INIT + re-emitted by forks), not
  // in the `spend` rollup — `spend` only carries what's been *used*.
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

/** One round's cost, split the way the money was actually spent. */
export interface RoundCostBucket {
  key: string;
  label: string;
  usd: number;
  // Which of the four things this bucket's prefix-cache reading is. A bucket holds billed calls
  // only, so `replayed` is false by construction.
  prefix: PrefixReading;
  // Input tokens billed at a premium to POPULATE the prefix cache. Writes with no reads is paying
  // to fill a prefix nothing collects.
  write: number;
}

export interface RoundCost {
  round: number;
  totalUsd: number;
  buckets: RoundCostBucket[];
}

/**
 * The per-round cost series, straight off the served `spend_by_round` map.
 *
 * The map is the projection's own fold — the SAME arithmetic that produces the cycle total, keyed
 * additionally by the round each call stamped itself with — so this groups and orders and computes
 * nothing. Until it existed, `dashboard.json::spend` was one running total for the whole cycle and
 * `rounds[]` carried no cost at all, so every round-axis surface showed a round with no price.
 *
 * Rounds are sorted numerically and non-numeric keys dropped; a round that billed nothing is
 * simply absent, which is what a bar chart wants.
 */
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
      buckets: SPEND_BUCKETS.map(({ key: k, label }) => {
        const b: SpendBucket = rollup[k];
        return {
          key: k,
          label,
          usd: b.used_usd,
          prefix: prefixReading(cacheShare(b.cache_read_tokens, b.input_tokens, false), false),
          write: b.cache_write_tokens,
        };
      }),
    });
  }
  return out.sort((a, b) => a.round - b.round);
}
