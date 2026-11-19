// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeStream } from "@/lib/__tests__/fake-stream";
import { DEFAULT_VIEWS, NO_KEYS, formatAddress } from "@/lib/address";
import { stepsRead } from "@/lib/api/reads";
import { clearReadCache, reaskRead } from "@/lib/read-cache";
import { useTimeRay } from "@/lib/state/poll";
import { InWorkspace as wrapper } from "@/lib/test-utils/in-workspace";

const path = [{ campaignId: "c1", cycleId: "root" }];

// The served ray: steps 0..length-1 by offset, windowed newest-first before an opaque cursor.
let length = 500;
function serve(url: string): Response {
  const q = new URL(url, "http://x").searchParams;
  const limit = Number(q.get("limit"));
  const bound = q.get("before") === null ? length : Number(q.get("before"));
  const first = Math.max(0, bound - limit);
  const steps = Array.from({ length: bound - first }, (_, i) => ({
    address: `s${first + i}`,
    facts: { offset: first + i },
  }));
  return Response.json({ steps, cursor_prev: first > 0 ? String(first) : null });
}

describe("useTimeRay — what is shown never slides as the head moves", () => {
  // Every window asked before a cursor: the head's own asks name none.
  let paged: string[];
  beforeEach(() => {
    length = 700;
    paged = [];
    vi.stubGlobal("EventSource", FakeStream);
    vi.stubGlobal("fetch", async (url: string) => {
      if (url.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      // The workspace's own reads (the listing, the active pointer) answer nothing here.
      if (!url.includes("limit=")) return new Response(null, { status: 404 });
      const before = new URL(url, "http://x").searchParams.get("before");
      if (before !== null) paged.push(before);
      return serve(url);
    });
    // The cycle the ray reads is the one the link pins.
    window.history.replaceState(null, "", formatAddress({ subject: { path }, views: DEFAULT_VIEWS, keys: NO_KEYS }));
    vi.useFakeTimers();
    clearReadCache(true);
  });
  afterEach(() => {
    cleanup();
    window.history.replaceState(null, "", window.location.pathname);
    clearReadCache(true);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  const settle = () => act(() => vi.advanceTimersByTimeAsync(0));
  const offsets = (steps: readonly { facts: { offset: number } }[]) => steps.map((s) => s.facts.offset);
  const contiguous = (from: number, to: number) => Array.from({ length: to - from }, (_, i) => from + i);

  const moveHead = async (to: number) => {
    length = to;
    act(() => reaskRead(stepsRead(path, 200, null).id));
    await settle();
    await settle();
  };

  it("the head moves by 30 under two pages: nothing shown goes, no gap, no repeat, one request", async () => {
    const { result } = renderHook(() => useTimeRay(), { wrapper });
    await settle();
    act(() => result.current.loadOlder());
    await settle();
    act(() => result.current.loadOlder());
    await settle();
    expect(offsets(result.current.steps)).toEqual(contiguous(100, 700));

    paged = [];
    await moveHead(730);
    expect(offsets(result.current.steps)).toEqual(contiguous(100, 730));
    // The pages keep their cursors: the move asks one bridge window, never the `pages` again.
    expect(paged).toEqual(["530"]);

    act(() => result.current.loadOlder());
    await settle();
    expect(offsets(result.current.steps)).toEqual(contiguous(0, 730));
    expect(result.current.hasMore).toBe(false);

    // Paged to the origin, a further move keeps the first steps.
    await moveHead(760);
    expect(offsets(result.current.steps)).toEqual(contiguous(0, 760));
  });
});
