"use client";
import { useState } from "react";

// Keeps the prior reference while the value is JSON-equal, so a memo survives poll identity churn.
export function useStableContent<T>(value: T): T {
  const key = JSON.stringify(value);
  const [stable, setStable] = useState<{ key: string; value: T }>(() => ({ key, value }));
  if (stable.key !== key) {
    setStable({ key, value });
    return value;
  }
  return stable.value;
}
