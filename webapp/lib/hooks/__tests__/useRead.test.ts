// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { AuthProvider, useAuth } from "@/lib/state/auth";
import { RECONNECT_POLL_MS } from "@/lib/cadence";
import { clearIncidents } from "@/lib/diagnostics";
import { useRead } from "@/lib/hooks/useRead";
import { GONE_CONFIRM_LIMIT, clearReadCache, reaskRead, type ReadDescriptor } from "@/lib/read-cache";

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(AuthProvider, null, children);

// A missed address is re-asked on the cache's reconnect beat.
const reconnect = () => act(() => vi.advanceTimersByTimeAsync(RECONNECT_POLL_MS));

describe("useRead — an address that stops existing", () => {
  beforeEach(() => {
    clearReadCache(true);
    clearIncidents();
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    // The session probe: an unreachable server reads as anonymous, which an ungated read ignores.
    vi.stubGlobal("fetch", () => Promise.reject(new Error("offline")));
  });
  afterEach(() => {
    cleanup();
    clearIncidents();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  const gone = (more: Partial<ReadDescriptor<string | null>> = {}) => {
    let calls = 0;
    const read: ReadDescriptor<string | null> = {
      id: "dashboard\x1f/gone",
      load: async () => {
        calls += 1;
        throw new ApiError(404, "/gone");
      },
      ...more,
    };
    return { read, calls: () => calls };
  };

  it("reports the address gone once, after the confirming misses, and stops its clock", async () => {
    const onGone = vi.fn();
    const { read, calls } = gone({ onGone });
    const { result } = renderHook(() => useRead(read), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(calls()).toBe(1);
    expect(onGone).not.toHaveBeenCalled();

    for (let miss = 2; miss <= GONE_CONFIRM_LIMIT; miss++) await reconnect();
    expect(calls()).toBe(GONE_CONFIRM_LIMIT);
    expect(onGone).toHaveBeenCalledTimes(1);
    expect(result.current).toMatchObject({ status: "failed", failure: { kind: "gone" } });

    await reconnect();
    await reconnect();
    expect(calls()).toBe(GONE_CONFIRM_LIMIT);
    expect(onGone).toHaveBeenCalledTimes(1);
  });

  it("reports once per id, however many components read it", async () => {
    const onGone = vi.fn();
    const { read } = gone({ onGone });
    renderHook(() => [useRead(read), useRead(read)], { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(0));
    for (let i = 0; i < GONE_CONFIRM_LIMIT + 1; i++) await reconnect();
    expect(onGone).toHaveBeenCalledTimes(1);
  });

  it("answers a read whose ordinary empty answer is a 404, and never retries it", async () => {
    const onGone = vi.fn();
    const { read, calls } = gone({ expects: "gone", onGone });
    const { result } = renderHook(() => useRead(read), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(0));
    for (let i = 0; i < GONE_CONFIRM_LIMIT + 1; i++) await reconnect();
    expect(calls()).toBe(1);
    expect(result.current).toEqual({ status: "ready", data: null });
    expect(onGone).not.toHaveBeenCalled();
  });

  it("starts the count again once the address answers", async () => {
    let calls = 0;
    const onGone = vi.fn();
    const read: ReadDescriptor<string> = {
      id: "dashboard\x1f/flaky",
      load: async () => {
        calls += 1;
        if (calls === GONE_CONFIRM_LIMIT) return { kind: "ok", data: "here", validator: null };
        throw new ApiError(404, "/flaky");
      },
      onGone,
    };
    renderHook(() => useRead(read), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(0));
    // Misses reconnect on the slow beat; the answer rests until its subject moves.
    for (let i = 1; i < GONE_CONFIRM_LIMIT; i++) await reconnect();
    act(() => reaskRead(read.id));
    await reconnect();
    // Two misses, an answer, two misses: never the limit running.
    expect(calls).toBe(GONE_CONFIRM_LIMIT + 2);
    expect(onGone).not.toHaveBeenCalled();
  });
});

describe("useRead — a throttled session probe", () => {
  const ME = { tenant_id: "t", user_id: "u" };
  let throttled = true;
  beforeEach(() => {
    clearReadCache(true);
    vi.useFakeTimers();
    throttled = true;
    vi.stubGlobal("fetch", async () =>
      throttled
        ? new Response(
            JSON.stringify({ code: "rate_limited", error_id: "e1", message: "Slow down.", details: {} }),
            { status: 429 },
          )
        : new Response(JSON.stringify(ME), { status: 200 }),
    );
  });
  afterEach(() => {
    cleanup();
    clearIncidents();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  // The request rate drained by polling tabs: a reload is never signed out, a session keeps its reads.
  it("keeps the identity and its gated reads, and re-probes an unprobed session", async () => {
    const gated: ReadDescriptor<string> = {
      id: "quota\x1f/q",
      auth: true,
      load: async () => ({ kind: "ok", data: "mine", validator: null }),
    };
    const { result } = renderHook(() => ({ auth: useAuth(), read: useRead(gated) }), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.auth.status).toBe("loading");

    throttled = false;
    await reconnect();
    expect(result.current.auth.status).toBe("authed");
    expect(result.current.read).toEqual({ status: "ready", data: "mine" });

    throttled = true;
    act(() => result.current.auth.refresh());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.auth).toMatchObject({ status: "authed", me: ME });
    expect(result.current.read).toEqual({ status: "ready", data: "mine" });
  });
});

describe("useRead — a session probe answered 404", () => {
  let probes = 0;
  beforeEach(() => {
    clearReadCache(true);
    vi.useFakeTimers();
    probes = 0;
    vi.stubGlobal("fetch", async () => {
      probes += 1;
      return new Response(null, { status: 404 });
    });
  });
  afterEach(() => {
    cleanup();
    clearIncidents();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  // No session route answers: the reload is signed out, never left loading on a re-probe.
  it("signs the reload out once, and does not probe again on the reconnect beat", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(result.current.status).toBe("unauthed");
    await reconnect();
    await reconnect();
    expect(probes).toBe(1);
  });
});

describe("useRead — a superseded id", () => {
  beforeEach(() => {
    clearReadCache(true);
    vi.stubGlobal("fetch", () => Promise.reject(new Error("offline")));
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const page = (id: string): ReadDescriptor<string> => ({
    id: `steps\x1f${id}`,
    load: async () => ({ kind: "ok", data: id, validator: null }),
  });

  // The time ray reads a page into state the pass its cursor changes: a pass that still showed the
  // old id's `ready` would add that page twice and lose the next.
  it("never shows the old id's data as ready on a pass for the new id", async () => {
    const seen: { id: string; read: string }[] = [];
    const { rerender } = renderHook(
      ({ id }: { id: string }) => {
        const read = useRead(page(id));
        seen.push({ id, read: read.status === "ready" ? read.data : read.status });
        return read;
      },
      { wrapper, initialProps: { id: "c1" } },
    );
    await act(async () => {});
    expect(seen.at(-1)).toEqual({ id: "c1", read: "c1" });

    rerender({ id: "c2" });
    await act(async () => {});
    const afterSwitch = seen.filter((s) => s.id === "c2");
    expect(afterSwitch.some((s) => s.read === "c1")).toBe(false);
    expect(afterSwitch.at(-1)).toEqual({ id: "c2", read: "c2" });
  });
});
