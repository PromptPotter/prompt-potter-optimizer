import { beforeEach, describe, expect, it } from "vitest";
import {
  READ_CACHE_MAX_KEYS,
  cachedRead,
  clearReadCache,
  readThrough,
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
    // The abandoned flight is not joined: the next reader starts its own.
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
