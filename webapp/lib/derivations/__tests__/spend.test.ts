import { describe, expect, it } from "vitest";
import { readSpend } from "../spend";
import type { SpendBucket } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";

// The prefix-cache discount is read PER BUCKET. It was one pooled ratio for a while, and the
// three buckets are not comparable quantities: a backend row carries ~86k input against a judge's
// ~1.6k, so the pool is the backend's share wearing everyone's name. Measured live at the time —
// backend 20.0%, judge 42.1%, optimizer 0.0% — the pooled number rounded the judge away, and the
// judge is the bucket whose rubric is a module constant and whose prefix pays best.
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
        // The live geometry: the backend dwarfs the other two, and the judge is the one holding
        // a real prefix. A pooled ratio here is 20_800/101_600 ≈ 20.5% — the backend's number,
        // to a decimal, with the judge invisible inside it.
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
