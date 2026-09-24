"use client";
// A mutation, or the dashboard poll seeing `run_phase` move, bumps this so every `usePoll` given
// `revalidateOn: useRevalidation()` re-ticks at once instead of waiting out its interval.

import { useSyncExternalStore } from "react";

let generation = 0;
const listeners = new Set<() => void>();

export function bumpRevalidation(): void {
  generation += 1;
  for (const l of listeners) l();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function getSnapshot(): number {
  return generation;
}

export function useRevalidation(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
