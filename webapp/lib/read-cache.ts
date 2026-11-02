import type { Conditional } from "@/lib/api/client";
import { READ_BEAT_MS } from "@/lib/cadence";

export const READ_CACHE_MAX_KEYS = 64;

export type ReadLoad<T> = (
  signal: AbortSignal,
  validator: string | null,
) => Promise<Conditional<T>>;

export interface ReadDescriptor<T> {
  id: string;
  load: ReadLoad<T>;
}

export function readName(id: string): string {
  return id.split("\x1f", 1)[0] ?? id;
}

interface Entry {
  data: unknown;
  validator: string | null;
}

interface Flight {
  promise: Promise<unknown>;
  abort: AbortController;
  waiters: number;
}

// Insertion order is recency.
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

interface ReadWatch {
  id: string;
  // null reads once, re-asked only by an invalidation.
  intervalMs: number | null;
  fire: () => void;
}

const watches = new Map<ReadWatch, number>();
let beat: ReturnType<typeof setInterval> | null = null;
let listening = false;

function nextDue(now: number, intervalMs: number): number {
  return (Math.floor(now / intervalMs) + 1) * intervalMs;
}

function hidden(): boolean {
  return typeof document !== "undefined" && document.hidden;
}

function onBeat(): void {
  const now = Date.now();
  for (const [watch, due] of watches) {
    if (watch.intervalMs === null || now < due) continue;
    watches.set(watch, nextDue(now, watch.intervalMs));
    watch.fire();
  }
}

// A flight in the air was asked before the change, so the re-ask flies fresh rather than joining it.
function reask(due: ReadWatch[]): void {
  for (const watch of due) flights.delete(watch.id);
  for (const watch of due) watch.fire();
}

// A change re-asks every mounted read it names, or every one, hidden tab or not: none may stand stale.
export function invalidateReads(name?: string): void {
  reask([...watches.keys()].filter((watch) => name === undefined || readName(watch.id) === name));
}

// On return, a one-shot read is re-asked only where a validator makes that free and identity-stable.
function revalidateReads(): void {
  if (hidden()) return;
  reask(
    [...watches.keys()].filter(
      (watch) => watch.intervalMs !== null || entries.get(watch.id)?.validator != null,
    ),
  );
}

function syncBeat(): void {
  const wanted = !hidden() && [...watches.keys()].some((w) => w.intervalMs !== null);
  if (wanted && beat === null) beat = setInterval(onBeat, READ_BEAT_MS);
  if (!wanted && beat !== null) {
    clearInterval(beat);
    beat = null;
  }
}

function listen(): void {
  if (listening || typeof document === "undefined") return;
  listening = true;
  document.addEventListener("visibilitychange", () => {
    syncBeat();
    revalidateReads();
  });
  window.addEventListener("focus", revalidateReads);
}

// The caller issues its own first read; the clock only re-asks.
export function watchRead(watch: ReadWatch): () => void {
  listen();
  watches.set(watch, watch.intervalMs === null ? 0 : nextDue(Date.now(), watch.intervalMs));
  syncBeat();
  return () => {
    watches.delete(watch);
    syncBeat();
  };
}
