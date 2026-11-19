// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { clearIncidents, getIncidents } from "@/lib/diagnostics";
import { useCommand } from "@/lib/hooks/useCommand";
import { clearReadCache, subscribe, type ReadDescriptor } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(AuthProvider, null, children);

const settle = () => act(() => vi.advanceTimersByTimeAsync(0));

describe("useCommand — a write re-asks no read: the feed says what it moved", () => {
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
    ["refused", async () => Promise.reject(new ApiError(409, "/write"))],
  ];
  for (const [answered, send] of sends) {
    it(`re-asks nothing ${answered}`, async () => {
      await settle();
      expect(asked).toEqual({ coupon: 1, hierarchy: 1 });
      const { result } = renderHook(() => useCommand<"write">("test", "A"), { wrapper });
      await act(async () => {
        await result.current.run("write", send);
      });
      await settle();
      expect(asked).toEqual({ coupon: 1, hierarchy: 1 });
    });
  }
});

describe("useCommand — a send's answer is the scope's it was sent from", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", () => Promise.reject(new Error("offline")));
  });
  afterEach(() => {
    cleanup();
    clearIncidents();
    vi.unstubAllGlobals();
  });

  const deferred = () => {
    let resolve!: (v: string) => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<string>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  };
  const mount = (scope: string) =>
    renderHook((p: { scope: string }) => useCommand<"write">("test", p.scope), {
      wrapper,
      initialProps: { scope },
    });

  const settles: [string, (d: ReturnType<typeof deferred>) => void][] = [
    ["landed", (d) => d.resolve("ok")],
    ["refused", (d) => d.reject(new ApiError(409, "/write"))],
  ];
  for (const [answered, answer] of settles) {
    it(`drops an answer ${answered} after the scope moved, and frees the slot`, async () => {
      const { result, rerender } = mount("A");
      const d = deferred();
      const then = vi.fn();
      let sent!: Promise<unknown>;
      act(() => {
        sent = result.current.run("write", () => d.promise, then);
      });
      expect(result.current.pending).toBe("write");
      rerender({ scope: "B" });
      expect(result.current.pending).toBeNull();
      // B's own send is admitted while A's is still in flight.
      const b = deferred();
      let sentB!: Promise<unknown>;
      act(() => {
        sentB = result.current.run("write", () => b.promise);
      });
      expect(result.current.pending).toBe("write");
      await act(async () => {
        answer(d);
        expect(await sent).toEqual({ ok: false, failure: null });
      });
      expect(then).not.toHaveBeenCalled();
      expect(result.current.failure).toBeNull();
      expect(result.current.pending).toBe("write");
      await act(async () => {
        b.resolve("ok");
        await sentB;
      });
      expect(result.current.pending).toBeNull();
    });

    it(`lands an answer ${answered} on an unmoved scope`, async () => {
      const { result } = mount("A");
      const d = deferred();
      const then = vi.fn();
      let sent!: Promise<unknown>;
      act(() => {
        sent = result.current.run("write", () => d.promise, then);
      });
      await act(async () => {
        answer(d);
        await sent;
      });
      expect(result.current.pending).toBeNull();
      if (answered === "landed") {
        expect(then).toHaveBeenCalledWith("ok");
        expect(result.current.failure).toBeNull();
      } else {
        expect(then).not.toHaveBeenCalled();
        expect(result.current.failure?.verb).toBe("write");
      }
    });
  }

  it("reports a caller's throwing `then` as one incident, never as the server's refusal", async () => {
    clearIncidents();
    const { result } = mount("A");
    let answer!: unknown;
    await act(async () => {
      answer = await result.current.run("write", async () => "ok", () => {
        throw new TypeError("caller bug");
      });
    });
    expect(answer).toEqual({ ok: true, value: "ok" });
    expect(result.current.failure).toBeNull();
    expect(result.current.pending).toBeNull();
    // The session probe's offline answer is an incident of its own surface.
    expect(getIncidents().filter((i) => i.surface === "test:write")).toHaveLength(1);
  });
});
