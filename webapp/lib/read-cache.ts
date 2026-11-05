import { failureKind, failurePasses, type Conditional, type FailureKind } from "@/lib/api/client";
import { operatorMessage } from "@/lib/api/errors";
import { READ_BEAT_MS, RECONNECT_POLL_MS } from "@/lib/cadence";
import { reportIncident } from "@/lib/diagnostics";

export const READ_CACHE_MAX_KEYS = 64;
export const GONE_CONFIRM_LIMIT = 3;

export type ReadLoad<T> = (
  signal: AbortSignal,
  validator: string | null,
) => Promise<Conditional<T>>;

export interface ReadFailure {
  kind: FailureKind;
  message: string;
}

export type ReadResult<T> =
  | { status: "idle" }
  | { status: "loading"; kept: T | null }
  | { status: "ready"; data: T }
  | { status: "failed"; failure: ReadFailure; kept: T | null };

// What a read's cadence is a function of: its own accepted body, and whether a write the browser
// sent that revalidates this read is still unanswered (it may be moving it). A failed read's
// cadence is the cache's own (`intervalOf`).
export interface CadenceState<T> {
  body: T | null;
  writing: boolean;
}

// A read names everything that varies per read; the cache runs each once per id.
export interface ReadDescriptor<T, B = T> {
  id: string;
  load: ReadLoad<B>;
  // Idle until the session is confirmed; a 401 re-probes it.
  auth?: boolean;
  // The key is not known yet: the read shows its keep body as `loading` and asks nothing.
  held?: true;
  // A 404 is this read's ordinary empty answer: it lands as a null body, answered like any other —
  // no incident, no gone verdict, and the poll goes on.
  expects?: null extends B ? "gone" : never;
  // Milliseconds between re-asks; null (or absent) reads once, re-asked only by an invalidation.
  cadence?: (state: CadenceState<T>) => number | null;
  // While this stays the same, a re-keyed read shows the last body the key landed as `kept`.
  keep?: string;
  // A body that is not the answer yet (a warming dashboard): the read stays `loading` on it.
  accept?: (body: B) => body is B & T;
  // After `GONE_CONFIRM_LIMIT` straight 404s: the poll stops and this runs, once per id.
  onGone?: () => void;
  // A landed body replaced an accepted one; once per id, whoever subscribed.
  onChange?: (prev: T, next: T) => void;
}

// The cache stores bodies untyped; each descriptor types its own id's.
type AnyRead = ReadDescriptor<unknown>;

export function readName(id: string): string {
  return id.split("\x1f", 1)[0] ?? id;
}

interface Flight {
  promise: Promise<unknown>;
  abort: AbortController;
  // External `readThrough` callers; a subscriber-driven flight has none.
  waiters: number;
}

interface Sub {
  read: () => AnyRead;
  notify: () => void;
}

interface Slot {
  id: string;
  // Who asks when no one subscribes: the last `readThrough`.
  asker: AnyRead;
  subs: Set<Sub>;
  entry: { data: unknown; validator: string | null } | null;
  failure: ReadFailure | null;
  misses: number;
  stopped: boolean;
  flight: Flight | null;
  interval: number | null;
  due: number;
  result: ReadResult<unknown>;
}

const IDLE: ReadResult<never> = { status: "idle" };

// Insertion order is recency.
const slots = new Map<string, Slot>();
const keeps = new Map<string, unknown>();

function descriptorOf(slot: Slot): AnyRead {
  let newest: Sub | undefined;
  for (const sub of slot.subs) newest = sub;
  return newest ? newest.read() : slot.asker;
}

function accepted(read: AnyRead, slot: Slot): { data: unknown } | null {
  const entry = slot.entry;
  if (entry === null) return null;
  return read.accept && !read.accept(entry.data) ? null : entry;
}

function resultOf(slot: Slot): ReadResult<unknown> {
  const read = descriptorOf(slot);
  const own = accepted(read, slot);
  const kept = own?.data ?? (read.keep !== undefined ? (keeps.get(read.keep) ?? null) : null);
  if (slot.failure) return { status: "failed", failure: slot.failure, kept };
  if (own) return { status: "ready", data: own.data };
  return { status: "loading", kept };
}

function changed(slot: Slot): void {
  slot.result = resultOf(slot);
  for (const sub of slot.subs) sub.notify();
  // The cadence is a function of the state that just moved.
  retime(slot);
  syncBeat();
}

// *spare* is the slot being handed out: idle only until its caller subscribes or flies it.
function evict(spare?: Slot): void {
  for (const map of [slots, keeps] as Map<string, unknown>[]) {
    for (const [id, held] of map) {
      if (map.size <= READ_CACHE_MAX_KEYS) break;
      const slot = held as Slot;
      if (map === slots && (slot === spare || slot.subs.size > 0 || slot.flight)) continue;
      map.delete(id);
    }
  }
}

function slotFor(read: AnyRead): Slot {
  let slot = slots.get(read.id);
  if (slot) {
    slots.delete(read.id);
    slots.set(read.id, slot);
    return slot;
  }
  slot = {
    id: read.id,
    asker: read,
    subs: new Set(),
    entry: null,
    failure: null,
    misses: 0,
    stopped: false,
    flight: null,
    interval: null,
    due: 0,
    result: IDLE,
  };
  slot.result = resultOf(slot);
  slots.set(read.id, slot);
  evict(slot);
  return slot;
}

function remember(read: AnyRead, data: unknown): void {
  if (read.keep === undefined) return;
  keeps.delete(read.keep);
  keeps.set(read.keep, data);
  evict();
  // A sibling id still loading under this key now keeps this body, mounted or not: a held slot
  // subscribed later shows the key's newest body, never the one it was made under.
  for (const slot of slots.values()) {
    if (slot.entry !== null) continue;
    if (descriptorOf(slot).keep === read.keep) changed(slot);
  }
}

function land(slot: Slot, data: unknown, validator: string | null): void {
  const read = descriptorOf(slot);
  const prev = accepted(read, slot);
  const same = slot.entry?.data === data && slot.failure === null;
  slot.entry = { data, validator };
  slot.failure = null;
  slot.misses = 0;
  if (same) return;
  const now = accepted(read, slot);
  changed(slot);
  if (now) remember(read, data);
  if (now && prev && prev.data !== data) read.onChange?.(prev.data, data);
}

function fail(slot: Slot, e: unknown): void {
  const read = descriptorOf(slot);
  const kind = failureKind(e);
  reportIncident(e, { surface: readName(slot.id), address: slot.id });
  if (kind === "gone") {
    // A body for an address that no longer exists must not paint the next mount.
    slot.entry = null;
    slot.misses += 1;
    if (slot.misses === GONE_CONFIRM_LIMIT) {
      slot.stopped = true;
      read.onGone?.();
    }
  } else {
    slot.misses = 0;
  }
  slot.failure = { kind, message: operatorMessage(e, kind) };
  changed(slot);
}

async function fly(slot: Slot, read: AnyRead, flight: Flight): Promise<unknown> {
  const mine = () => slot.flight === flight && slots.get(slot.id) === slot;
  try {
    const signal = flight.abort.signal;
    let res = await read.load(signal, slot.entry?.validator ?? null);
    if (res.kind === "not_modified") {
      const held = slot.entry;
      if (held) {
        if (mine()) land(slot, held.data, held.validator);
        return held.data;
      }
      // The body was dropped or cleared mid-flight, so the 304 describes nothing held.
      res = await read.load(signal, null);
      if (res.kind === "not_modified") throw new Error(`304 to an unconditional read: ${slot.id}`);
    }
    if (mine()) land(slot, res.data, res.validator);
    return res.data;
  } catch (e) {
    if (flight.abort.signal.aborted) throw e;
    // The read's ordinary empty answer: a null body, to its waiters as to its subscribers.
    if (failureKind(e) === read.expects) {
      if (mine()) land(slot, null, null);
      return null;
    }
    if (mine()) fail(slot, e);
    throw e;
  } finally {
    if (slot.flight === flight) slot.flight = null;
  }
}

function start(slot: Slot, read: AnyRead): Flight {
  const flight: Flight = { promise: Promise.resolve(), abort: new AbortController(), waiters: 0 };
  slot.flight = flight;
  flight.promise = fly(slot, read, flight);
  // Subscribers read the slot, not the promise.
  flight.promise.catch(() => {});
  return flight;
}

// The flight in the air was asked before a change: it still lands for its own waiters, stores
// nothing, and is aborted only when none of them is left.
function supersede(slot: Slot): void {
  const old = slot.flight;
  if (!old) return;
  slot.flight = null;
  if (old.waiters === 0) old.abort.abort();
}

function ask(slot: Slot, fresh: boolean): void {
  if (descriptorOf(slot).held) return;
  if (fresh) supersede(slot);
  if (!slot.flight) start(slot, descriptorOf(slot));
}

// `signal` is the CALLER's: it leaves the flight, and only the last waiter leaving aborts it.
export function readThrough<T, B>(read: ReadDescriptor<T, B>, signal: AbortSignal): Promise<B> {
  const slot = slotFor(read as unknown as AnyRead);
  slot.asker = read as unknown as AnyRead;
  const flight = slot.flight ?? start(slot, slot.asker);
  flight.waiters += 1;
  const leave = (): void => {
    flight.waiters -= 1;
    if (flight.waiters > 0 || (slot.flight === flight && slot.subs.size > 0)) return;
    if (slot.flight === flight) slot.flight = null;
    flight.abort.abort();
  };
  signal.addEventListener("abort", leave, { once: true });
  return flight.promise.finally(() => signal.removeEventListener("abort", leave)) as Promise<B>;
}

export function readResult<T, B>(read: ReadDescriptor<T, B> | null): ReadResult<T> {
  if (read === null) return IDLE;
  return slotFor(read as unknown as AnyRead).result as ReadResult<T>;
}

// The address's body is forgotten: whoever reads it next asks.
export function dropCachedRead(id: string): void {
  const slot = slots.get(id);
  if (!slot) return;
  if (slot.subs.size === 0) {
    supersede(slot);
    slots.delete(id);
    return;
  }
  slot.entry = null;
  changed(slot);
}

// An identity flip: every body, kept body, verdict and flight belongs to the last identity. A
// mounted read is re-asked under the new one; signed out, an auth read is left to `useRead`'s gate.
export function clearReadCache(signedIn: boolean): void {
  keeps.clear();
  for (const slot of [...slots.values()]) {
    supersede(slot);
    if (slot.subs.size === 0) {
      slots.delete(slot.id);
      continue;
    }
    slot.entry = null;
    slot.failure = null;
    slot.misses = 0;
    slot.stopped = false;
    changed(slot);
    if (signedIn || !descriptorOf(slot).auth) ask(slot, false);
  }
}

let beat: ReturnType<typeof setInterval> | null = null;
let listening = false;
// Each unanswered write, by the read names it revalidates; null names every read.
const writes = new Set<{ reads: ReadonlySet<string> | null }>();

function writingOn(slot: Slot): boolean {
  const name = readName(slot.id);
  for (const write of writes) if (write.reads === null || write.reads.has(name)) return true;
  return false;
}

function nextDue(now: number, intervalMs: number): number {
  return (Math.floor(now / intervalMs) + 1) * intervalMs;
}

function hidden(): boolean {
  return typeof document !== "undefined" && document.hidden;
}

// The fastest any subscriber asks: a read is polled once per id, at the most eager cadence. A
// failed read is the cache's (a read's expected 404 is its answer, not a failure): a failure that
// passes (`failurePasses`), and a 404 until `GONE_CONFIRM_LIMIT` confirms it, re-asks no faster
// than the reconnect beat nor than the read's own cadence; a refusal the server would repeat rests
// until a change re-asks it.
function intervalOf(slot: Slot): number | null {
  if (slot.stopped || slot.subs.size === 0) return null;
  const read = descriptorOf(slot);
  const failed = slot.failure?.kind ?? null;
  if (failed !== null && failed !== "gone" && !failurePasses(failed)) return null;
  const own = accepted(read, slot);
  const state: CadenceState<unknown> = { body: own?.data ?? null, writing: writingOn(slot) };
  let fastest: number | null = null;
  for (const sub of slot.subs) {
    const ms = sub.read().cadence?.(state) ?? null;
    if (ms !== null && (fastest === null || ms < fastest)) fastest = ms;
  }
  if (failed === null) return fastest;
  return fastest === null ? RECONNECT_POLL_MS : Math.max(fastest, RECONNECT_POLL_MS);
}

// Due on the next wall-clock multiple of a new interval, so reads of one cadence share a beat.
function retime(slot: Slot): void {
  const ms = intervalOf(slot);
  if (ms === slot.interval) return;
  slot.interval = ms;
  if (ms !== null) slot.due = nextDue(Date.now(), ms);
}

function onBeat(): void {
  const now = Date.now();
  for (const slot of slots.values()) {
    // A subscriber's newest descriptor may name another cadence without anything landing.
    retime(slot);
    const ms = slot.interval;
    if (ms === null || now < slot.due) continue;
    slot.due = nextDue(now, ms);
    ask(slot, false);
  }
}

function mounted(): Slot[] {
  return [...slots.values()].filter((slot) => slot.subs.size > 0 && !slot.stopped);
}

// A change re-asks every mounted read it names, or every one, hidden tab or not: none may stand stale.
export function invalidateReads(name?: string): void {
  for (const slot of mounted()) {
    if (name === undefined || readName(slot.id) === name) ask(slot, true);
  }
}

// On return or reconnect, a polled read (a recovering one included) is re-asked at once rather
// than on its beat, and an answered one-shot read where a validator makes that free and
// identity-stable; a refused read rests either way.
function revalidateReads(): void {
  if (hidden()) return;
  for (const slot of mounted()) {
    if (intervalOf(slot) !== null || (slot.failure === null && slot.entry?.validator != null)) {
      ask(slot, true);
    }
  }
}

// A write the browser sent, in flight on the reads it revalidates (`reads`, by name; null is every
// read): until it is answered, their cadences see `writing`. The answer's own re-ask is the
// writer's (`useCommand`); a cadence may rest meanwhile (`reads.ts::draftCadence`).
export function beginWrite(reads: readonly string[] | null): () => void {
  const write = { reads: reads === null ? null : new Set(reads) };
  writes.add(write);
  retimeAll();
  return () => {
    if (!writes.delete(write)) return;
    retimeAll();
  };
}

function retimeAll(): void {
  for (const slot of slots.values()) retime(slot);
  syncBeat();
}

function syncBeat(): void {
  const wanted = !hidden() && [...slots.values()].some((slot) => intervalOf(slot) !== null);
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
  window.addEventListener("online", revalidateReads);
}

// `read` is the subscriber's newest descriptor: its cadence counts, its effects run. The first
// subscriber of an id asks; the others share its flight and body.
export function subscribe(read: () => AnyRead, notify: () => void): () => void {
  listen();
  const slot = slotFor(read());
  const sub: Sub = { read, notify };
  const first = slot.subs.size === 0;
  slot.subs.add(sub);
  if (first) {
    slot.stopped = false;
    slot.misses = 0;
    ask(slot, false);
  }
  retime(slot);
  syncBeat();
  return () => {
    slot.subs.delete(sub);
    if (slot.subs.size === 0) supersede(slot);
    retime(slot);
    syncBeat();
    evict();
  };
}
