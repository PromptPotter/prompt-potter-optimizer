// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeStream } from "@/lib/__tests__/fake-stream";
import { RECONNECT_POLL_MS } from "@/lib/cadence";
import { useCycleEvents } from "@/lib/hooks/useCycleEvents";
import { clearReadCache } from "@/lib/read-cache";
import { InWorkspace, anonymous } from "@/lib/test-utils/in-workspace";

// The server's snapshot of a cycle, its activity's line.
const snapshot = (tail: FakeStream, cycleId: string, line: string): void =>
  tail.send({ cycle_id: cycleId, activity: { status: { text: line }, notices: [], decision: null } });

describe("useCycleEvents — a refused tail reopens on the reconnect beat", () => {
  beforeEach(() => {
    FakeStream.opened = [];
    vi.stubGlobal("EventSource", FakeStream);
    vi.stubGlobal("fetch", anonymous);
    clearReadCache(false);
    vi.useFakeTimers();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("reopens a CLOSED stream after the beat and re-reads its snapshot", async () => {
    const path = [{ campaignId: "c1", cycleId: "root" }];
    const { result, unmount } = renderHook(() => useCycleEvents(path), { wrapper: InWorkspace });
    expect(FakeStream.opened).toHaveLength(1);
    const first = FakeStream.opened[0]!;
    act(() => {
      first.onopen?.();
      snapshot(first, "root", "before");
    });
    expect(result.current.connected).toBe(true);
    expect(result.current.status).toEqual({ text: "before" });

    // A dropped stream reconnects by itself: no second door.
    act(() => first.onerror?.());
    expect(result.current.connected).toBe(false);
    await act(() => vi.advanceTimersByTimeAsync(RECONNECT_POLL_MS));
    expect(FakeStream.opened).toHaveLength(1);

    // A refused one is closed for good and reopened on the beat.
    act(() => {
      first.readyState = FakeStream.CLOSED;
      first.onerror?.();
    });
    await act(() => vi.advanceTimersByTimeAsync(RECONNECT_POLL_MS));
    expect(FakeStream.opened).toHaveLength(2);
    const second = FakeStream.opened[1]!;
    act(() => {
      second.onopen?.();
      snapshot(second, "root", "after");
    });
    expect(result.current.connected).toBe(true);
    expect(result.current.status).toEqual({ text: "after" });

    unmount();
    expect(second.readyState).toBe(FakeStream.CLOSED);
  });
});
