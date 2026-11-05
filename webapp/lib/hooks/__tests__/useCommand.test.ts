// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { clearIncidents } from "@/lib/diagnostics";
import { useCommand } from "@/lib/hooks/useCommand";
import { clearReadCache, subscribe, type ReadDescriptor } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(AuthProvider, null, children);

const settle = () => act(() => vi.advanceTimersByTimeAsync(0));

describe("useCommand — a write's answer re-asks the reads it names, and no other", () => {
  const asked = { coupon: 0, hierarchy: 0 };
  const mounted: (() => void)[] = [];

  beforeEach(() => {
    clearReadCache(true);
    clearIncidents();
    vi.useFakeTimers();
    vi.setSystemTime(10_000);
    // The session probe: an unreachable server reads as anonymous, which an ungated read ignores.
    vi.stubGlobal("fetch", () => Promise.reject(new Error("offline")));
    asked.coupon = 0;
    asked.hierarchy = 0;
    for (const name of ["coupon", "hierarchy"] as const) {
      const read: ReadDescriptor<number> = {
        id: `${name}\x1f/${name}`,
        load: async () => ({ kind: "ok", data: (asked[name] += 1), validator: null }),
        cadence: () => null,
      };
      mounted.push(subscribe(() => read as unknown as ReadDescriptor<unknown>, () => {}));
    }
  });
  afterEach(() => {
    for (const unwatch of mounted.splice(0)) unwatch();
    cleanup();
    clearReadCache(true);
    clearIncidents();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  const sends: [string, () => Promise<string>][] = [
    ["landed", async () => "ok"],
    // The sends before a refused one may have moved what the write names.
    ["refused", async () => Promise.reject(new ApiError(409, "/write"))],
  ];
  for (const [answered, send] of sends) {
    it(`re-asks exactly the named reads once ${answered}`, async () => {
      await settle();
      expect(asked).toEqual({ coupon: 1, hierarchy: 1 });
      const { result } = renderHook(() => useCommand<"write">("test", { revalidate: ["coupon"] }), {
        wrapper,
      });
      await act(async () => {
        await result.current.run("write", send);
      });
      await settle();
      expect(asked).toEqual({ coupon: 2, hierarchy: 1 });
    });
  }
});
