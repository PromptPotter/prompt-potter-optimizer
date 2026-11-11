import {
  API,
  failureKind,
  failurePasses,
  type Conditional,
  type FailureKind,
  type FeedVersions,
} from "@/lib/api/client";
import { operatorMessage } from "@/lib/api/errors";
import { READ_ROUTES, type FeedFrame } from "@/lib/api/types";
import { RECONNECT_POLL_MS } from "@/lib/cadence";
import { reportIncident } from "@/lib/diagnostics";

export const READ_CACHE_MAX_KEYS = 64;
export const GONE_CONFIRM_LIMIT = 3;

// *subject* is the descriptor's: a loader sends it, and its answer says the versions it stood on.
export type ReadLoad<T> = (
  signal: AbortSignal,
  validator: string | null,
  subject?: string,
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

// The one ready result: a cached body's, or a body another transport of the same answer holds.
export function heldRead<T>(data: T): ReadResult<T> {
  return { status: "ready", data };
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
  // no incident, no gone verdict.
  expects?: null extends B ? "gone" : never;
  // The feed subject whose move re-asks it, by prefix (`FeedFrame.subject`: `store:` and the hops
  // above the store it reads, or `machine`); absent, it reads once.
  subject?: string;
  // While this stays the same, a re-keyed read shows the last body the key landed as `kept`.
  keep?: string;
  // A body that is not the answer yet (a warming dashboard): the read stays `loading` on it.
  accept?: (body: B) => body is B & T;
  // After `GONE_CONFIRM_LIMIT` straight 404s: the retry stops and this runs, once per id.
  onGone?: () => void;
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
  result: ReadResult<unknown>;
  // The feed versions its last answer stood on; null where none said them.
  stood: FeedVersions | null;
  // Each frame that came while it flew: its landing re-asks where it stood on another version.
  owed: Map<string, string>;
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
  if (own) return heldRead(own.data);
  return { status: "loading", kept };
}

function changed(slot: Slot): void {
  slot.result = resultOf(slot);
  for (const sub of slot.subs) sub.notify();
  syncRetry();
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
    result: IDLE,
    stood: null,
    owed: new Map(),
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
  const same = slot.entry?.data === data && slot.failure === null;
  slot.entry = { data, validator };
  slot.failure = null;
  slot.misses = 0;
  if (same) return;
  changed(slot);
  if (accepted(read, slot)) remember(read, data);
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
    let res = await read.load(signal, slot.entry?.validator ?? null, read.subject);
    if (mine()) slot.stood = res.stood ?? null;
    if (res.kind === "not_modified") {
      const held = slot.entry;
      if (held) {
        if (mine()) land(slot, held.data, held.validator);
        return held.data;
      }
      // The body was dropped or cleared mid-flight, so the 304 describes nothing held.
      res = await read.load(signal, null, read.subject);
      if (res.kind === "not_modified") throw new Error(`304 to an unconditional read: ${slot.id}`);
      if (mine()) slot.stood = res.stood ?? null;
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
    if (slot.flight === flight) {
      slot.flight = null;
      const owed = [...slot.owed];
      slot.owed.clear();
      if (slot.subs.size > 0 && owed.some(([subject, version]) => slot.stood?.[subject] !== version)) {
        ask(slot, true);
      }
    }
  }
}

function start(slot: Slot, read: AnyRead): Flight {
  const flight: Flight = { promise: Promise.resolve(), abort: new AbortController(), waiters: 0 };
  // Asked after every frame owed so far: its answer stands on them.
  slot.owed.clear();
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

// An identity flip: every body, kept body, verdict, flight and feed version belongs to the last
// identity. A mounted read is re-asked under the new one; signed out, an auth read is left to
// `useRead`'s gate.
export function clearReadCache(signedIn: boolean): void {
  keeps.clear();
  closeFeed();
  for (const slot of [...slots.values()]) {
    supersede(slot);
    if (slot.subs.size === 0) {
      slots.delete(slot.id);
      continue;
    }
    slot.entry = null;
    slot.stood = null;
    slot.failure = null;
    slot.misses = 0;
    slot.stopped = false;
    changed(slot);
    if (signedIn || !descriptorOf(slot).auth) ask(slot, false);
  }
  if (mounted().length > 0) openFeed();
}

function mounted(): Slot[] {
  return [...slots.values()].filter((slot) => slot.subs.size > 0 && !slot.stopped);
}

// A failure that passes (`failurePasses`), and a 404 until `GONE_CONFIRM_LIMIT` confirms it, is
// re-asked on the reconnect beat; a refusal the server would repeat rests until its subject moves.
function retrying(slot: Slot): boolean {
  const failed = slot.failure?.kind ?? null;
  return failed !== null && (failed === "gone" || failurePasses(failed));
}

let retry: ReturnType<typeof setInterval> | null = null;

function syncRetry(): void {
  const wanted = mounted().some(retrying);
  if (wanted && retry === null) {
    retry = setInterval(() => {
      for (const slot of mounted()) if (retrying(slot)) ask(slot, false);
    }, RECONNECT_POLL_MS);
  }
  if (!wanted && retry !== null) {
    clearInterval(retry);
    retry = null;
  }
}

// The operator's retry of a mounted read: asked again now, whatever its subject.
export function reaskRead(id: string): void {
  const slot = slots.get(id);
  if (slot && slot.subs.size > 0) ask(slot, true);
}

// The server says what moved (`GET /feed`): each subject at its version, then each move. A frame is
// a hint, never the body: it re-asks every mounted read whose subject it starts and whose answer
// stood on another version of it, and what the read answers is what the cache keeps. Compared with
// the version each answer stood on, never the frame before: an answer read before the feed's first
// frame misses no move between them.
let feed: EventSource | null = null;

function moved(subject: string, version: string): void {
  for (const slot of mounted()) {
    const named = descriptorOf(slot).subject;
    if (named === undefined || !subject.startsWith(named)) continue;
    if (slot.flight) slot.owed.set(subject, version);
    else if (slot.stood?.[subject] !== version) ask(slot, true);
  }
}

function openFeed(): void {
  if (feed !== null || typeof EventSource === "undefined") return;
  const es = new EventSource(`${API}${READ_ROUTES.feed}`, { withCredentials: true });
  feed = es;
  es.onmessage = (ev: MessageEvent<string>) => {
    let frame: FeedFrame;
    try {
      frame = JSON.parse(ev.data) as FeedFrame;
    } catch (e) {
      // The server serializes every frame, so one that does not parse is an incident, not noise.
      reportIncident(e, { surface: "feed" });
      return;
    }
    moved(frame.subject, frame.version);
  };
  // A dropped stream reconnects by itself, and its first frames say what moved meanwhile; a
  // refused one (a 401, a 5xx) is closed for good and reopened on the reconnect beat.
  es.onerror = () => {
    if (es.readyState !== EventSource.CLOSED || feed !== es) return;
    feed = null;
    setTimeout(openFeed, RECONNECT_POLL_MS);
  };
}

function closeFeed(): void {
  feed?.close();
  feed = null;
}

// `read` is the subscriber's newest descriptor: its subject counts, its effects run. The first
// subscriber of an id asks; the others share its flight and body.
export function subscribe(read: () => AnyRead, notify: () => void): () => void {
  openFeed();
  const slot = slotFor(read());
  const sub: Sub = { read, notify };
  const first = slot.subs.size === 0;
  slot.subs.add(sub);
  if (first) {
    slot.stopped = false;
    slot.misses = 0;
    ask(slot, false);
  }
  syncRetry();
  return () => {
    slot.subs.delete(sub);
    if (slot.subs.size === 0) supersede(slot);
    syncRetry();
    evict();
  };
}
