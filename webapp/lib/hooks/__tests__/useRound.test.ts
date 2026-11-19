// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeStream } from "@/lib/__tests__/fake-stream";
import { roundRead } from "@/lib/api/reads";
import type { RoundView } from "@/lib/api/types";
import { useRound } from "@/lib/hooks/useRound";
import { clearReadCache, reaskRead } from "@/lib/read-cache";
import { roundOfArms } from "@/lib/test-fixtures";
import { InWorkspace as wrapper } from "@/lib/test-utils/in-workspace";

const path = [{ campaignId: "c1", cycleId: "root" }];

// The account's feed, driven by the test: a live claim stamps the store's version every tick.
const tick = (version: number): void =>
  FakeStream.opened.findLast((f) => f.readyState !== FakeStream.CLOSED)?.frame("store:", version);

const A = { key: "a", label: "C1.0" };
const B = { key: "b", label: "C1.1" };
// The server's two halves: in flight as announced, filed in rank order with its headline. Neither
// lists the parent the round re-read (`cycle_reads.py::served_round`).
const IN_FLIGHT: RoundView = { ...roundOfArms(1, [A, B]), unfiled: true };
const FILED: RoundView = {
  ...roundOfArms(1, [B, A]),
  headline: roundOfArms(1, [B]).candidates[0]!.reading,
};

describe("useRound — a round reads the round read alone, in flight and filed", () => {
  let served: RoundView;
  let asked: string[];
  beforeEach(() => {
    served = IN_FLIGHT;
    asked = [];
    FakeStream.opened = [];
    vi.stubGlobal("EventSource", FakeStream);
    vi.stubGlobal("fetch", async (url: string) => {
      if (url.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      const asking = new URL(url, "http://x").pathname;
      // The workspace's own reads (the listing, the active pointer) answer nothing here.
      if (!asking.includes("/rounds/")) return new Response(null, { status: 404 });
      asked.push(asking);
      return Response.json(served);
    });
    vi.useFakeTimers();
    clearReadCache(true);
  });
  afterEach(() => {
    cleanup();
    clearReadCache(true);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  const settle = () => act(() => vi.advanceTimersByTimeAsync(0));
  const keys = (arms: { reading: { arm: { key: string } } }[]) => arms.map((a) => a.reading.arm.key);

  it("a round that files mid-view keeps its arms and their count, read off one descriptor", async () => {
    const { result } = renderHook(() => useRound(path, 1), { wrapper });
    await settle();
    expect(result.current.unfiled).toBe(true);
    expect(keys(result.current.arms)).toEqual(["a", "b"]);

    served = FILED;
    act(() => reaskRead(roundRead(path, 1).id));
    expect(keys(result.current.arms)).toEqual(["a", "b"]);
    await settle();
    expect(result.current.unfiled).toBe(false);
    // The strip counts the same arms across the file; only the served rank orders them.
    expect(keys(result.current.arms)).toEqual(["b", "a"]);
    expect(result.current.reading("b")?.arm.label).toBe("C1.1");

    // Every ask was the round's own: no dashboard read stands in for its arms.
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every((p) => p.endsWith("/rounds/1"))).toBe(true);
  });

  it("an arm's start is one object per answer, so an editor seeded off it keeps its edit", async () => {
    const { result, rerender } = renderHook(() => useRound(path, 1), { wrapper });
    await settle();
    const seeded = result.current.start("a");
    expect(seeded).not.toBeNull();
    rerender();
    expect(result.current.start("a")).toBe(seeded);
  });

  it("under a ticking live store the round in flight re-asks and a filed one asks nothing", async () => {
    const { result } = renderHook(() => useRound(path, 1), { wrapper });
    await settle();
    const opened = asked.length;
    tick(1);
    await settle();
    expect(asked.length).toBe(opened + 1);

    served = FILED;
    tick(2);
    await settle();
    expect(result.current.unfiled).toBe(false);
    const filed = asked.length;
    for (const version of [3, 4, 5]) {
      tick(version);
      await settle();
    }
    // Its rows re-read the archive, so it reads once, as a round file did: no rebuild per tick.
    expect(asked.length).toBe(filed);
    expect(keys(result.current.arms)).toEqual(["b", "a"]);
  });
});
