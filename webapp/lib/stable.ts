"use client";
import { useState } from "react";

// Return the prior reference when the new value is structurally equal.
// Lets memo consumers depend on `value` directly instead of hand-rolling a
// content fingerprint to defend against poll-driven identity churn. Compare
// is JSON.stringify-based — adequate for the polled dashboard.json
// projections (~50 KB at the largest); if a hot loop ever needs sub-ms
// equality, swap to a shape-aware compare per call-site.
//
// Implemented via the render-phase guarded reset pattern (webapp/CLAUDE.md):
// state holds {key, value}, render compares the incoming key to the stored
// one and setState's on mismatch.
export function useStableContent<T>(value: T): T {
  const key = JSON.stringify(value);
  const [stable, setStable] = useState<{ key: string; value: T }>(() => ({ key, value }));
  if (stable.key !== key) {
    setStable({ key, value });
    return value;
  }
  return stable.value;
}
