"use client";
import { useEffect, useState } from "react";

// Debounce a fast-changing value: returns the latest value only after it has
// held still for `delayMs`. Used to keep a slider's continuous drag from
// spamming a server fetch (the bars recompute live; the served lineage overlay
// catches up once the drag settles).
export function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}
