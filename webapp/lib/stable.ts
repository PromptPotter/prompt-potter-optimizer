"use client";
import { useState } from "react";

export function useStableContent<T>(value: T): T {
  const key = JSON.stringify(value);
  const [stable, setStable] = useState<{ key: string; value: T }>(() => ({ key, value }));
  if (stable.key !== key) {
    setStable({ key, value });
    return value;
  }
  return stable.value;
}
