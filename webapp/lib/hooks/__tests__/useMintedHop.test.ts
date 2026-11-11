// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CommandReading, CycleHop } from "@/lib/api/types";
import { useMintedHop } from "@/lib/hooks/useMintedHop";
import { clearReadCache } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";

// The browser's `EventSource`, driven by the test: the server's frames.
class FakeFeed {
  static CLOSED = 2;
  static opened: FakeFeed[] = [];
  readyState = 1;
  onmessage: ((ev: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeFeed.opened.push(this);
  }
  close(): void {
    this.readyState = FakeFeed.CLOSED;
  }
  frame(subject: string, version: string): void {
    this.onmessage?.({ data: JSON.stringify({ subject, version }) } as MessageEvent<string>);
  }
}

const wrapper = ({ children }: { children: ReactNode }) => createElement(AuthProvider, null, children);
const settle = () => act(() => vi.advanceTimersByTimeAsync(0));

describe("a queued Start follows its command on the feed to the campaign a slot mints", () => {
  const minted: CycleHop = { campaign_id: "camp_new", cycle_id: "cyc_1" };
  let hop: CycleHop | null;
  let asked: number;

  const reading = (): CommandReading => ({
    command_id: "cmd_1",
    kind: "start-checkin",
    subject: "draft:d1",
    status: "working",
    said: "start-checkin: queued",
    created_at: "2026-11-11T00:00:00Z",
    last_updated_at: "2026-11-11T00:00:00Z",
    launches: [{ job_id: "job_1", hop, what: "the check-in's run" }],
  });

  beforeEach(() => {
    FakeFeed.opened = [];
    vi.stubGlobal("EventSource", FakeFeed);
    clearReadCache(true);
    vi.useFakeTimers();
    hop = null;
    asked = 0;
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/commands/cmd_1")) {
        asked += 1;
        return Response.json(reading());
      }
      throw new Error(`unrouted: ${url}`);
    });
  });
  afterEach(() => {
    cleanup();
    clearReadCache(true);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("lands on the minted hop when its store moves, with no reload and no clock", async () => {
    const { result } = renderHook(() => useMintedHop("cmd_1"), { wrapper });
    await settle();
    expect(asked).toBe(1);
    expect(result.current).toBeNull();
    const [feed] = FakeFeed.opened;
    if (!feed) throw new Error("no feed opened");
    act(() => feed.frame("store:", "v1"));
    // Nothing asks on a clock: a queued launch waits on the feed alone.
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(asked).toBe(1);
    // A slot frees: the job names its cycle, and the minted run's appends move the store.
    hop = minted;
    act(() => feed.frame("store:", "v2"));
    await settle();
    expect(asked).toBe(2);
    expect(result.current).toEqual(minted);
  });
});
