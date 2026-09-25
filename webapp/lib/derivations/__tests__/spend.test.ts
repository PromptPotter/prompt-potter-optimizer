import { describe, expect, it } from "vitest";
import { readSpend, roundCosts } from "../spend";
import type { SpendBucket } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";

// The prefix-cache discount is read PER BUCKET: a backend row carries far more input than a
// judge's, so a pooled ratio is the backend's share wearing everyone's name.
function bucket(over: Partial<SpendBucket> = {}): SpendBucket {
  return {
    used_usd: 0,
    input_tokens: 0,
    output_tokens: 0,
    reasoning_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    rate_known: false,
    model: null,
    unpriced_tokens: 0,
    incurred_usd: 0,
    incurred_unpriced_tokens: 0,
    ...over,
  };
}

function dash(buckets: {
  backend?: Partial<SpendBucket>;
  loop?: Partial<SpendBucket>;
  judge?: Partial<SpendBucket>;
}): DashboardSnapshot {
  return {
    spend: {
      backend: bucket(buckets.backend),
      loop: bucket(buckets.loop),
      judge: bucket(buckets.judge),
      total_used_usd: 0,
      total_incurred_usd: 0,
      total_tokens_used: 0,
      unpriced_tokens: 0,
    },
  } as unknown as DashboardSnapshot;
}

describe("readSpend prefix-cache shares", () => {
  it("reads each bucket on its own input, never on the pool", () => {
    const view = readSpend(
      dash({
        // The backend dwarfs the other two: pooled, this reads ≈ the backend's number alone.
        backend: { input_tokens: 100_000, cache_read_tokens: 20_000 },
        loop: { input_tokens: 0, cache_read_tokens: 0 },
        judge: { input_tokens: 1_600, cache_read_tokens: 800 },
      }),
    );

    expect(view.backendCacheShare).toBeCloseTo(0.2, 10);
    expect(view.judgeCacheShare).toBeCloseTo(0.5, 10);
    // A bucket that has billed no input has no share to state — not 0%, which would claim a
    // provider answered and discounted nothing.
    expect(view.loopCacheShare).toBeNull();
  });

  it("keeps a measured zero apart from a bucket with nothing billed", () => {
    const view = readSpend(
      dash({
        backend: { input_tokens: 5_000, cache_read_tokens: 0 },
      }),
    );

    // The provider served 5k input and discounted none of it. That is a reading, and it is what
    // "this provider has no prefix cache" looks like from here.
    expect(view.backendCacheShare).toBe(0);
    expect(view.judgeCacheShare).toBeNull();
  });

  it("has no share at all before a spend block exists", () => {
    const view = readSpend(null);
    expect(view.backendCacheShare).toBeNull();
    expect(view.loopCacheShare).toBeNull();
    expect(view.judgeCacheShare).toBeNull();
  });
});

describe("roundCosts over a round missing a bucket", () => {
  it("drops a bucket that round's file never carried", () => {
    // `dashboard.json` is served VERBATIM, so a round can lack a bucket the generated type
    // declares; reading it through the annotation throws where no type check can see.
    const snapshot = {
      spend_by_round: {
        "0": {
          backend: bucket({ used_usd: 0.5, input_tokens: 1_000, cache_read_tokens: 100 }),
          loop: bucket({ used_usd: 0.01 }),
          judge: bucket({ used_usd: 0.02 }),
          total_used_usd: 0.53,
          total_incurred_usd: 0.53,
          total_tokens_used: 1_000,
          unpriced_tokens: 0,
        },
      },
    } as unknown as DashboardSnapshot;

    const [round0] = roundCosts(snapshot);
    expect(round0?.buckets.map((b) => b.key)).toEqual(["backend", "loop", "judge"]);
    // The total is SERVED and needs no bucket to be present, so it survives the absence intact —
    // which is what makes the missing row a gap the reader can see rather than a wrong number.
    expect(round0?.totalUsd).toBe(0.53);
  });
});
