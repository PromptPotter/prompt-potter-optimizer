import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { READ_BEAT_MS, RECONNECT_POLL_MS } from "@/lib/cadence";
import {
  READ_CACHE_MAX_KEYS,
  beginWrite,
  clearReadCache,
  invalidateReads,
  readName,
  readResult,
  readThrough,
  subscribe,
  type ReadDescriptor,
  type ReadLoad,
} from "@/lib/read-cache";

const live = (): AbortSignal => new AbortController().signal;

function body<T>(data: T, validator: string | null = null): ReadLoad<T> {
  return async () => ({ kind: "ok", data, validator });
}

const at = <T>(id: string, load: ReadLoad<T>, more: Partial<ReadDescriptor<T>> = {}) => ({
  id,
  load,
  ...more,
});

// A load that lands only when the test says so, counting its asks.
function held<T>() {
  const lands: ((data: T) => void)[] = [];
  const load: ReadLoad<T> = () =>
    new Promise((resolve) => lands.push((data) => resolve({ kind: "ok", data, validator: null })));
  return { lands, load };
}

// What the next reader of `id` is shown as answered, through the read interface.
function stored(id: string): unknown {
  const read = readResult(at(id, body(null)));
  return read.status === "ready" ? read.data : null;
}

describe("read cache", () => {
  beforeEach(() => clearReadCache(true));

  it("serves the held body to the next reader, and a 304 revalidation keeps it", async () => {
    expect(stored("k")).toBeNull();
    await readThrough(at("k", body({ n: 1 }, 'W/"a"')), live());
    const kept = stored("k");
    expect(kept).toEqual({ n: 1 });

    const sent: (string | null)[] = [];
    const revalidated = await readThrough(
      at<{ n: number }>("k", async (_signal, validator) => {
        sent.push(validator);
        return { kind: "not_modified" };
      }),
      live(),
    );
    expect(sent).toEqual(['W/"a"']);
    expect(revalidated).toBe(kept);

    await readThrough(at("k", body({ n: 2 })), live());
    expect(stored("k")).toEqual({ n: 2 });
  });

  it("issues one fetch for concurrent readers, aborted only when the last one leaves", async () => {
    let calls = 0;
    const flown: AbortSignal[] = [];
    let land: (v: string) => void = () => {};
    const load: ReadLoad<string> = (signal) => {
      calls += 1;
      flown.push(signal);
      return new Promise((resolve) => {
        land = (data) => resolve({ kind: "ok", data, validator: null });
      });
    };
    const first = new AbortController();
    const a = readThrough(at("k", load), first.signal);
    const b = readThrough(at("k", load), live());
    expect(calls).toBe(1);

    first.abort();
    expect(flown[0]!.aborted).toBe(false);
    land("v");
    expect(await Promise.all([a, b])).toEqual(["v", "v"]);

    const only = new AbortController();
    void readThrough(at("gone", load), only.signal).catch(() => {});
    only.abort();
    expect(flown[1]!.aborted).toBe(true);
    void readThrough(at("gone", load), live());
    expect(calls).toBe(3);
  });

  it("evicts the least recently stored key past its bound", async () => {
    for (let i = 0; i <= READ_CACHE_MAX_KEYS; i++) {
      await readThrough(at(`k${i}`, body(i)), live());
      if (i === 1) await readThrough(at("k0", body(0)), live());
    }
    // Asking an absent id makes a slot, so the evicted one is asked last.
    expect(stored("k0")).toBe(0);
    expect(stored(`k${READ_CACHE_MAX_KEYS}`)).toBe(READ_CACHE_MAX_KEYS);
    expect(stored("k1")).toBeNull();
  });

  it("stores nothing from a flight that was in the air when the identity changed", async () => {
    const { lands, load } = held<string>();
    const pending = readThrough(at("k", load), live());
    clearReadCache(true);
    lands[0]!("theirs");
    expect(await pending).toBe("theirs");
    expect(stored("k")).toBeNull();
  });
});

describe("read cache — subscribers", () => {
  const mounted: (() => void)[] = [];
  const watch = <T, B = T>(read: ReadDescriptor<T, B>) => {
    const seen = { notified: 0 };
    const descriptor = read as unknown as ReadDescriptor<unknown>;
    mounted.push(subscribe(() => descriptor, () => (seen.notified += 1)));
    return seen;
  };
  const settle = () => vi.advanceTimersByTimeAsync(0);

  beforeEach(() => {
    clearReadCache(true);
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
  });
  afterEach(() => {
    for (const unwatch of mounted.splice(0)) unwatch();
    clearReadCache(true);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("names a read by its id's first segment", () => {
    expect(readName("quota\x1f/api/v1/auth/quota-status")).toBe("quota");
  });

  it("asks once per id however many subscribe, and lands the body for every one", async () => {
    const { lands, load } = held<number>();
    const read = at("a\x1fu", load);
    watch(read);
    watch(read);
    expect(lands).toHaveLength(1);
    lands[0]!(7);
    await settle();
    expect(readResult(read)).toEqual({ status: "ready", data: 7 });
  });

  it("lands a new subscriber's body past the bound, every older id still live", async () => {
    for (let i = 0; i < READ_CACHE_MAX_KEYS; i++) watch(at(`live${i}`, held<number>().load));
    const read = at("late", body(7));
    watch(read);
    await settle();
    expect(readResult(read)).toEqual({ status: "ready", data: 7 });
  });

  it("polls on wall-clock multiples of the most eager subscriber's cadence", async () => {
    let calls = 0;
    const load: ReadLoad<number> = async () => ({ kind: "ok", data: ++calls, validator: null });
    watch(at("a\x1fu", load, { cadence: () => 4 * READ_BEAT_MS }));
    watch(at("a\x1fu", load, { cadence: () => 2 * READ_BEAT_MS }));
    await settle();
    expect(calls).toBe(1);
    // 12 000 is the next multiple of the 2-beat cadence after mounting at 10 000.
    await vi.advanceTimersByTimeAsync(2 * READ_BEAT_MS);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(2 * READ_BEAT_MS);
    expect(calls).toBe(3);
  });

  it("reads the cadence off the read's own state, and stops beating when none polls", async () => {
    let calls = 0;
    const load: ReadLoad<{ settled: boolean }> = async () => ({
      kind: "ok",
      data: { settled: ++calls >= 2 },
      validator: null,
    });
    watch(at("t\x1fu", load, { cadence: (s) => (s.body?.settled ? null : READ_BEAT_MS) }));
    await settle();
    await vi.advanceTimersByTimeAsync(READ_BEAT_MS);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(3 * READ_BEAT_MS);
    expect(calls).toBe(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("re-asks a mounted read on an invalidation of its name, and no other", async () => {
    let quota = 0;
    let cycles = 0;
    watch(at("quota\x1fu", async () => ({ kind: "ok", data: ++quota, validator: null })));
    watch(at("cycles\x1fu", async () => ({ kind: "ok", data: ++cycles, validator: null })));
    await settle();
    invalidateReads("quota");
    await settle();
    expect([quota, cycles]).toEqual([2, 1]);
    invalidateReads();
    await settle();
    expect([quota, cycles]).toEqual([3, 2]);
  });

  it("re-asks in a fresh flight that never deletes another caller's", async () => {
    const { lands, load } = held<number>();
    const read = at("draft\x1fu", load);
    const theirs = readThrough(read, live());
    watch(read);
    invalidateReads("draft");
    expect(lands).toHaveLength(2);
    lands[1]!(2);
    lands[0]!(1);
    expect(await theirs).toBe(1);
    await settle();
    expect(readResult(read)).toEqual({ status: "ready", data: 2 });
  });

  it("keeps a re-keyed read's last body under its keep key while the new id loads", async () => {
    const first = at("d\x1f1", body("one"), { keep: "d" });
    watch(first);
    await settle();
    const { load } = held<string>();
    const second = at("d\x1f2", load, { keep: "d" });
    watch(second);
    expect(readResult(second)).toEqual({ status: "loading", kept: "one" });
  });

  it("holds a read whose key is not known yet on its keep body, asking nothing", async () => {
    watch(at("d\x1f1", body("one"), { keep: "d" }));
    await settle();
    let asked = 0;
    const pending = at("d\x1fseeding", async () => ({ kind: "ok", data: `x${++asked}`, validator: null }), {
      keep: "d",
      held: true,
    });
    watch(pending);
    invalidateReads();
    await settle();
    expect(asked).toBe(0);
    expect(readResult(pending)).toEqual({ status: "loading", kept: "one" });
  });

  it("times a read on a write that revalidates it while it is unanswered, and rests it on the answer", async () => {
    const asked = { moved: 0, other: 0 };
    const count = (k: keyof typeof asked): ReadLoad<number> =>
      async () => ({ kind: "ok", data: ++asked[k], validator: null });
    const onWrite = { cadence: (s: { writing: boolean }) => (s.writing ? 2 * READ_BEAT_MS : null) };
    watch(at("draft\x1fu", count("moved"), onWrite));
    watch(at("dashboard\x1fu", count("other"), onWrite));
    await settle();
    await vi.advanceTimersByTimeAsync(6 * READ_BEAT_MS);
    expect(asked).toEqual({ moved: 1, other: 1 });

    const answered = beginWrite(["draft"]);
    await vi.advanceTimersByTimeAsync(4 * READ_BEAT_MS);
    expect(asked).toEqual({ moved: 3, other: 1 });
    answered();
    await vi.advanceTimersByTimeAsync(6 * READ_BEAT_MS);
    expect(asked).toEqual({ moved: 3, other: 1 });

    const everything = beginWrite(null);
    await vi.advanceTimersByTimeAsync(2 * READ_BEAT_MS);
    everything();
    expect(asked).toEqual({ moved: 4, other: 2 });
  });

  it("re-asks a read lost to the network or throttled on the slow beat, and rests one the server refused", async () => {
    const asked = { lost: 0, denied: 0, empty: 0, slow: 0, fast: 0, throttled: 0, capped: 0 };
    // The request-rate throttle passes; a spend cap, a 429 too, would answer the same again.
    const throttled = at("t\x1fu", async () => {
      asked.throttled += 1;
      throw new ApiError(429, "/t", "rate_limited");
    });
    const capped = at("c\x1fu", async () => {
      asked.capped += 1;
      throw new ApiError(429, "/c", "spend_cap");
    });
    // A read polled faster than the reconnect beat backs off to it while it fails.
    const fast = at(
      "f\x1fu",
      async () => {
        asked.fast += 1;
        throw new Error("offline");
      },
      { cadence: () => READ_BEAT_MS },
    );
    const lost = at("l\x1fu", async () => {
      if (++asked.lost === 1) throw new Error("offline");
      return { kind: "ok", data: asked.lost, validator: null };
    });
    // A read slower than the reconnect beat is never asked faster for having failed.
    const slow = at(
      "s\x1fu",
      async () => {
        asked.slow += 1;
        throw new Error("offline");
      },
      { cadence: () => 12 * RECONNECT_POLL_MS },
    );
    const denied = at("d\x1fu", async () => {
      asked.denied += 1;
      throw new ApiError(403, "/d");
    });
    // A 404 is this one's empty answer, so its own cadence stands.
    const empty = at(
      "e\x1fu",
      async () => {
        asked.empty += 1;
        throw new ApiError(404, "/e");
      },
      { expects: "gone", cadence: () => 2 * READ_BEAT_MS },
    );
    watch(lost);
    watch(denied);
    watch(empty);
    watch(slow);
    watch(fast);
    watch(throttled);
    watch(capped);
    await settle();
    expect(asked).toEqual({ lost: 1, denied: 1, empty: 1, slow: 1, fast: 1, throttled: 1, capped: 1 });

    await vi.advanceTimersByTimeAsync(RECONNECT_POLL_MS);
    expect(readResult(lost)).toEqual({ status: "ready", data: 2 });
    expect(asked).toEqual({ lost: 2, denied: 1, empty: 3, slow: 1, fast: 2, throttled: 2, capped: 1 });
  });

  it("shows a held read subscribed again the key's newest body, not the one it was made under", async () => {
    watch(at("d\x1f1", body("one"), { keep: "d" }));
    await settle();
    const seeding = at("d\x1fseeding", body("never"), { keep: "d", held: true });
    const unwatch = subscribe(() => seeding as ReadDescriptor<unknown>, () => {});
    expect(readResult(seeding)).toEqual({ status: "loading", kept: "one" });
    unwatch();

    watch(at("d\x1f2", body("two"), { keep: "d" }));
    await settle();
    watch(seeding);
    expect(readResult(seeding)).toEqual({ status: "loading", kept: "two" });
  });

  it("stays loading on a body the read does not accept", async () => {
    type Warm = { warming_up: true } | { n: number };
    const read: ReadDescriptor<{ n: number }, Warm> = {
      id: "w\x1fu",
      load: body<Warm>({ warming_up: true }),
      accept: (b): b is { n: number } => !("warming_up" in b),
    };
    watch(read);
    await settle();
    expect(readResult(read)).toEqual({ status: "loading", kept: null });
  });

  it("runs a change effect once per landed change, however many subscribe", async () => {
    let n = 0;
    const onChange = vi.fn();
    const read = at("c\x1fu", async () => ({ kind: "ok", data: ++n, validator: null }), {
      onChange,
    });
    watch(read);
    watch(read);
    await settle();
    expect(onChange).not.toHaveBeenCalled();
    invalidateReads("c");
    await settle();
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(1, 2);
  });

  it("clears what a subscriber kept on an identity flip, and re-asks it", async () => {
    let n = 0;
    const read = at("me\x1fu", async () => ({ kind: "ok", data: ++n, validator: null }), {
      keep: "me",
    });
    watch(read);
    await settle();
    clearReadCache(true);
    expect(readResult(read)).toEqual({ status: "loading", kept: null });
    await settle();
    expect(readResult(read)).toEqual({ status: "ready", data: 2 });
  });

  it("on a flip to signed out, re-asks a public read and leaves an auth read to the gate", async () => {
    const asked = { open: 0, gated: 0 };
    const count = (k: keyof typeof asked): ReadLoad<number> =>
      async () => ({ kind: "ok", data: ++asked[k], validator: null });
    const gated = at("quota\x1fu", count("gated"), { auth: true });
    watch(at("health\x1fu", count("open")));
    watch(gated);
    await settle();
    clearReadCache(false);
    await settle();
    expect(asked).toEqual({ open: 2, gated: 1 });
    expect(readResult(gated)).toEqual({ status: "loading", kept: null });
  });

  it("stops the beat under a hidden tab, and re-asks on return what a poll, a validator or a failure leaves", async () => {
    const page = { hidden: false, onVisibility: () => {} };
    const on: Record<string, () => void> = {};
    vi.stubGlobal("document", {
      get hidden() {
        return page.hidden;
      },
      addEventListener: (_: string, handler: () => void) => (page.onVisibility = handler),
    });
    vi.stubGlobal("window", { addEventListener: (name: string, handler: () => void) => (on[name] = handler) });
    const asked = { polled: 0, validated: 0, bare: 0, lost: 0, denied: 0, empty: 0, revoked: 0 };
    const count = (k: keyof typeof asked, validator: string | null): ReadLoad<number> =>
      async () => ({ kind: "ok", data: ++asked[k], validator });
    const refusing = (k: keyof typeof asked, status: number): ReadLoad<number | null> =>
      async () => {
        asked[k] += 1;
        throw new ApiError(status, `/${k}`);
      };
    // A one-shot read whose first ask fails (the network was down): a return asks it at once.
    const lostRead = at("l\x1fu", async () => {
      if (++asked.lost === 1) throw new Error("offline");
      return { kind: "ok", data: asked.lost, validator: null };
    });
    watch(at("p\x1fu", count("polled", null), { cadence: () => READ_BEAT_MS }));
    watch(at("v\x1fu", count("validated", 'W/"a"')));
    watch(at("b\x1fu", count("bare", null)));
    watch(lostRead);
    // A refusal the server would repeat, and a one-shot read's expected empty answer: neither is
    // asked again for a return or a reconnect.
    watch(at("d\x1fu", refusing("denied", 403)));
    watch(at("e\x1fu", refusing("empty", 404), { expects: "gone" }));
    // Answered with a validator, then refused (access revoked): its held validator buys no re-ask.
    watch(
      at("r\x1fu", async () => {
        if (++asked.revoked === 1) return { kind: "ok", data: 1, validator: 'W/"r"' };
        throw new ApiError(403, "/r");
      }),
    );
    await settle();
    invalidateReads("r");
    await settle();
    expect(asked).toEqual({ polled: 1, validated: 1, bare: 1, lost: 1, denied: 1, empty: 1, revoked: 2 });
    expect(readResult(lostRead).status).toBe("failed");

    page.hidden = true;
    page.onVisibility();
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(5 * READ_BEAT_MS);
    page.hidden = false;
    page.onVisibility();
    await settle();
    expect(asked).toEqual({ polled: 2, validated: 2, bare: 1, lost: 2, denied: 1, empty: 1, revoked: 2 });
    expect(readResult(lostRead)).toEqual({ status: "ready", data: 2 });

    // Reconnecting re-asks the same way; a failure answered since is not re-asked again.
    on.online!();
    await settle();
    expect(asked).toEqual({ polled: 3, validated: 3, bare: 1, lost: 2, denied: 1, empty: 1, revoked: 2 });
  });
});
