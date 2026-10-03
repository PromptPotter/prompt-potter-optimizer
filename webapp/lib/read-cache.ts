// The module store behind `useRead`: the last body of each read, and ONE flight per read however
// many instances ask. Bodies belong to an identity, so `auth-context` clears it when that moves.

import type { Conditional } from "@/lib/api";

export const READ_CACHE_MAX_KEYS = 64;

export type ReadLoad<T> = (
  signal: AbortSignal,
  validator: string | null,
) => Promise<Conditional<T>>;

interface Entry {
  data: unknown;
  validator: string | null;
}

interface Flight {
  promise: Promise<unknown>;
  abort: AbortController;
  waiters: number;
}

// Insertion order is recency: a store re-inserts, and every paint from here revalidates.
const entries = new Map<string, Entry>();
const flights = new Map<string, Flight>();

function store(id: string, data: unknown, validator: string | null): void {
  entries.delete(id);
  entries.set(id, { data, validator });
  for (const oldest of entries.keys()) {
    if (entries.size <= READ_CACHE_MAX_KEYS) break;
    entries.delete(oldest);
  }
}

export function cachedRead<T>(id: string): { data: T } | null {
  const hit = entries.get(id);
  return hit ? { data: hit.data as T } : null;
}

export function dropCachedRead(id: string): void {
  entries.delete(id);
}

// A flight in the air still lands for its waiters but stores nothing: `flights` disowns it.
export function clearReadCache(): void {
  entries.clear();
  flights.clear();
}

async function fly<T>(id: string, load: ReadLoad<T>, flight: Flight): Promise<T> {
  try {
    const signal = flight.abort.signal;
    let res = await load(signal, entries.get(id)?.validator ?? null);
    if (res.kind === "not_modified") {
      const kept = entries.get(id);
      if (kept) {
        if (flights.get(id) === flight) store(id, kept.data, kept.validator);
        return kept.data as T;
      }
      // The body was evicted or cleared mid-flight, so the 304 describes nothing held.
      res = await load(signal, null);
      if (res.kind === "not_modified") throw new Error(`304 to an unconditional read: ${id}`);
    }
    if (flights.get(id) === flight) store(id, res.data, res.validator);
    return res.data;
  } finally {
    if (flights.get(id) === flight) flights.delete(id);
  }
}

// `signal` is the CALLER's: it leaves the flight, and only the last waiter leaving aborts it.
export function readThrough<T>(id: string, load: ReadLoad<T>, signal: AbortSignal): Promise<T> {
  let flight = flights.get(id);
  if (!flight) {
    const started: Flight = { promise: Promise.resolve(), abort: new AbortController(), waiters: 0 };
    flights.set(id, started);
    started.promise = fly(id, load, started);
    flight = started;
  }
  const joined = flight;
  joined.waiters += 1;
  const leave = (): void => {
    joined.waiters -= 1;
    if (joined.waiters > 0 || flights.get(id) !== joined) return;
    flights.delete(id);
    joined.abort.abort();
  };
  signal.addEventListener("abort", leave, { once: true });
  return joined.promise.finally(() => signal.removeEventListener("abort", leave)) as Promise<T>;
}
