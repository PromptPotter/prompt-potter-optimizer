// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { AuthProvider } from "@/lib/auth-context";
import { clearIncidents } from "@/lib/diagnostics";
import { GONE_CONFIRM_LIMIT, useRead } from "@/lib/hooks/useRead";
import { READ_BEAT_MS, clearReadCache, type ReadDescriptor } from "@/lib/read-cache";

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(AuthProvider, null, children);

const beat = () => act(() => vi.advanceTimersByTimeAsync(READ_BEAT_MS));

describe("useRead — an address that stops existing", () => {
  beforeEach(() => {
    clearReadCache();
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

  const gone = (): { read: ReadDescriptor<string>; calls: () => number } => {
    let calls = 0;
    return {
      calls: () => calls,
      read: {
        id: "dashboard\x1f/gone",
        load: async () => {
          calls += 1;
          throw new ApiError(404, "/gone");
        },
      },
    };
  };

  it("reports the address gone once, after the confirming misses, and stops its clock", async () => {
    const { read, calls } = gone();
    const onGone = vi.fn();
    const { result } = renderHook(() => useRead(read, { intervalMs: READ_BEAT_MS, onGone }), {
      wrapper,
    });
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(calls()).toBe(1);
    expect(onGone).not.toHaveBeenCalled();

    for (let miss = 2; miss <= GONE_CONFIRM_LIMIT; miss++) await beat();
    expect(calls()).toBe(GONE_CONFIRM_LIMIT);
    expect(onGone).toHaveBeenCalledTimes(1);
    expect(result.current).toMatchObject({ status: "failed", failure: { kind: "gone" } });

    await beat();
    await beat();
    expect(calls()).toBe(GONE_CONFIRM_LIMIT);
    expect(onGone).toHaveBeenCalledTimes(1);
  });

  it("keeps polling a read whose ordinary empty answer is a 404", async () => {
    const { read, calls } = gone();
    const onGone = vi.fn();
    renderHook(() => useRead(read, { intervalMs: READ_BEAT_MS, expects: "gone", onGone }), {
      wrapper,
    });
    await act(() => vi.advanceTimersByTimeAsync(0));
    for (let i = 0; i < GONE_CONFIRM_LIMIT + 1; i++) await beat();
    expect(calls()).toBe(GONE_CONFIRM_LIMIT + 2);
    expect(onGone).not.toHaveBeenCalled();
  });

  it("starts the count again once the address answers", async () => {
    let calls = 0;
    const read: ReadDescriptor<string> = {
      id: "dashboard\x1f/flaky",
      load: async () => {
        calls += 1;
        if (calls === GONE_CONFIRM_LIMIT) return { kind: "ok", data: "here", validator: null };
        throw new ApiError(404, "/flaky");
      },
    };
    const onGone = vi.fn();
    renderHook(() => useRead(read, { intervalMs: READ_BEAT_MS, onGone }), { wrapper });
    await act(() => vi.advanceTimersByTimeAsync(0));
    for (let i = 0; i < GONE_CONFIRM_LIMIT + 1; i++) await beat();
    // Two misses, an answer, two misses: never the limit running.
    expect(calls).toBe(GONE_CONFIRM_LIMIT + 2);
    expect(onGone).not.toHaveBeenCalled();
  });
});
