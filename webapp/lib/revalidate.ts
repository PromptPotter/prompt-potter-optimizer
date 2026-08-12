"use client";
// Revalidation seam. A mutating call (fork / stop / cleanup) bumps a generation
// counter on success — and so does the dashboard poll on seeing `run_phase` MOVE,
// since that one server-owned value also rides `/cycles` and `/tree` at slower
// cadences and would otherwise leave three surfaces up to 10 s apart. Poll loops
// that pass
// `revalidateOn: useRevalidation()` into `usePoll` re-tick immediately
// instead of waiting out their interval — so a new fork shows up at once,
// not up to a poll-interval later. Module-scoped, mirroring candidates-store.ts.

import { useSyncExternalStore } from "react";

let generation = 0;
const listeners = new Set<() => void>();

// Call after a mutation resolves. Pure signal — touches no I/O itself.
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
