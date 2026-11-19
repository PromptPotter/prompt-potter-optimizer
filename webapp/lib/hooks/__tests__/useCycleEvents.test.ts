// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RECONNECT_POLL_MS } from "@/lib/cadence";
import { useCycleEvents } from "@/lib/hooks/useCycleEvents";

vi.mock("@/lib/state/workspace", () => ({ useWorkspace: () => ({ goneAddress: null }) }));

// The browser's `EventSource`, driven by the test: the server's snapshot, a refusal.
class FakeTail {
  static CLOSED = 2;
  static opened: FakeTail[] = [];
  readyState = 1;
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeTail.opened.push(this);
  }
  close(): void {
    this.readyState = FakeTail.CLOSED;
  }
  snapshot(cycleId: string, line: string): void {
    const activity = { status: { text: line }, notices: [], decision: null };
    this.onmessage?.({ data: JSON.stringify({ cycle_id: cycleId, activity }) } as MessageEvent<string>);
  }
}

describe("useCycleEvents — a refused tail reopens on the reconnect beat", () => {
  beforeEach(() => {
    FakeTail.opened = [];
    vi.stubGlobal("EventSource", FakeTail);
    vi.useFakeTimers();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("reopens a CLOSED stream after the beat and re-reads its snapshot", async () => {
    const path = [{ campaignId: "c1", cycleId: "root" }];
    const { result, unmount } = renderHook(() => useCycleEvents(path));
    expect(FakeTail.opened).toHaveLength(1);
    const first = FakeTail.opened[0]!;
    act(() => {
      first.onopen?.();
      first.snapshot("root", "before");
    });
    expect(result.current.connected).toBe(true);
    expect(result.current.status).toEqual({ text: "before" });

    // A dropped stream reconnects by itself: no second door.
    act(() => first.onerror?.());
    expect(result.current.connected).toBe(false);
    await act(() => vi.advanceTimersByTimeAsync(RECONNECT_POLL_MS));
    expect(FakeTail.opened).toHaveLength(1);

    // A refused one is closed for good and reopened on the beat.
    act(() => {
      first.readyState = FakeTail.CLOSED;
      first.onerror?.();
    });
    await act(() => vi.advanceTimersByTimeAsync(RECONNECT_POLL_MS));
    expect(FakeTail.opened).toHaveLength(2);
    const second = FakeTail.opened[1]!;
    act(() => {
      second.onopen?.();
      second.snapshot("root", "after");
    });
    expect(result.current.connected).toBe(true);
    expect(result.current.status).toEqual({ text: "after" });

    unmount();
    expect(second.readyState).toBe(FakeTail.CLOSED);
  });
});
