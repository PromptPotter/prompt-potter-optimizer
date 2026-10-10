import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  READ_BEAT_MS,
  READ_CACHE_MAX_KEYS,
  cachedRead,
  clearReadCache,
  invalidateReads,
  readName,
  readThrough,
  watchRead,
  type ReadLoad,
} from "@/lib/read-cache";

const live = (): AbortSignal => new AbortController().signal;

function body<T>(data: T, validator: string | null = null): ReadLoad<T> {
  return async () => ({ kind: "ok", data, validator });
}

describe("read cache", () => {
  beforeEach(clearReadCache);

  it("serves the held body to the next mount, and a 304 revalidation keeps it", async () => {
    expect(cachedRead("k")).toBeNull();
    await readThrough("k", body({ n: 1 }, 'W/"a"'), live());
    const held = cachedRead<{ n: number }>("k")?.data;
    expect(held).toEqual({ n: 1 });

    const sent: (string | null)[] = [];
    const revalidated = await readThrough<{ n: number }>(
      "k",
      async (_signal, validator) => {
        sent.push(validator);
        return { kind: "not_modified" };
      },
      live(),
    );
    expect(sent).toEqual(['W/"a"']);
    expect(revalidated).toBe(held);

    await readThrough("k", body({ n: 2 }), live());
    expect(cachedRead("k")?.data).toEqual({ n: 2 });
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
    const a = readThrough("k", load, first.signal);
    const b = readThrough("k", load, live());
    expect(calls).toBe(1);

    first.abort();
    expect(flown[0]!.aborted).toBe(false);
    land("v");
    expect(await Promise.all([a, b])).toEqual(["v", "v"]);

    const only = new AbortController();
    void readThrough("gone", load, only.signal).catch(() => {});
    only.abort();
    expect(flown[1]!.aborted).toBe(true);
    void readThrough("gone", load, live());
    expect(calls).toBe(3);
  });

  it("evicts the least recently stored key past its bound", async () => {
    for (let i = 0; i <= READ_CACHE_MAX_KEYS; i++) {
      await readThrough(`k${i}`, body(i), live());
      if (i === 1) await readThrough("k0", body(0), live());
    }
    expect(cachedRead("k1")).toBeNull();
    expect(cachedRead("k0")?.data).toBe(0);
    expect(cachedRead(`k${READ_CACHE_MAX_KEYS}`)?.data).toBe(READ_CACHE_MAX_KEYS);
  });

  it("stores nothing from a flight that was in the air when the identity changed", async () => {
    let land: () => void = () => {};
    const pending = readThrough<string>(
      "k",
      () =>
        new Promise((resolve) => {
          land = () => resolve({ kind: "ok", data: "theirs", validator: null });
        }),
      live(),
    );
    clearReadCache();
    land();
    expect(await pending).toBe("theirs");
    expect(cachedRead("k")).toBeNull();
  });
});

describe("read clock", () => {
  const mounted: (() => void)[] = [];
  const watch = (id: string, intervalMs: number | null): { fired: number } => {
    const seen = { fired: 0 };
    mounted.push(watchRead({ id, intervalMs, fire: () => (seen.fired += 1) }));
    return seen;
  };

  beforeEach(() => {
    clearReadCache();
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
  });
  afterEach(() => {
    for (const unwatch of mounted.splice(0)) unwatch();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("names a read by its id's first segment", () => {
    expect(readName("quota\x1f/api/v1/auth/quota-status")).toBe("quota");
  });

  it("fires a watch on wall-clock multiples of its interval, whenever it mounted", () => {
    const early = watch("a\x1fu", 2 * READ_BEAT_MS);
    vi.advanceTimersByTime(500);
    const late = watch("b\x1fu", 2 * READ_BEAT_MS);
    vi.advanceTimersByTime(1000);
    expect([early.fired, late.fired]).toEqual([0, 0]);
    // 12 000 is the next multiple for both, so they land on one beat.
    vi.advanceTimersByTime(500);
    expect([early.fired, late.fired]).toEqual([1, 1]);
    vi.advanceTimersByTime(2 * READ_BEAT_MS);
    expect([early.fired, late.fired]).toEqual([2, 2]);
  });

  it("never fires a one-shot on the beat, and stops beating once no polled watch is mounted", () => {
    const once = watch("a\x1fu", null);
    const polled = watch("b\x1fu", READ_BEAT_MS);
    vi.advanceTimersByTime(3 * READ_BEAT_MS);
    expect([once.fired, polled.fired]).toEqual([0, 3]);
    for (const unwatch of mounted.splice(0)) unwatch();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("unnamed, re-asks polled reads and only the one-shots a validator makes free", async () => {
    await readThrough("held\x1fu", body(1, 'W/"a"'), live());
    await readThrough("bare\x1fu", body(1), live());
    const polled = watch("polled\x1fu", 5 * READ_BEAT_MS);
    const held = watch("held\x1fu", null);
    const bare = watch("bare\x1fu", null);
    invalidateReads();
    expect([polled.fired, held.fired, bare.fired]).toEqual([1, 1, 0]);
  });

  it("named, re-asks every read of that endpoint and no other", () => {
    const bare = watch("quota\x1fu", null);
    const sibling = watch("quota\x1fv", 5 * READ_BEAT_MS);
    const other = watch("cycles\x1fu", 5 * READ_BEAT_MS);
    invalidateReads("quota");
    expect([bare.fired, sibling.fired, other.fired]).toEqual([1, 1, 0]);
  });

  it("stops the beat under a hidden tab, ignores invalidation there, and re-asks on return", () => {
    const page = { hidden: false, onVisibility: () => {} };
    vi.stubGlobal("document", {
      get hidden() {
        return page.hidden;
      },
      addEventListener: (_: string, handler: () => void) => (page.onVisibility = handler),
    });
    vi.stubGlobal("window", { addEventListener: () => {} });
    const polled = watch("a\x1fu", READ_BEAT_MS);
    vi.advanceTimersByTime(READ_BEAT_MS);
    expect(polled.fired).toBe(1);

    page.hidden = true;
    page.onVisibility();
    expect(vi.getTimerCount()).toBe(0);
    invalidateReads();
    vi.advanceTimersByTime(5 * READ_BEAT_MS);
    expect(polled.fired).toBe(1);

    page.hidden = false;
    page.onVisibility();
    expect(polled.fired).toBe(2);
    vi.advanceTimersByTime(READ_BEAT_MS);
    expect(polled.fired).toBe(3);
  });
});
