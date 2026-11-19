// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { commands } from "@/lib/api/commands";
import { userSettingsRead } from "@/lib/api/reads";
import { FEED_VERSIONS_HEADER, type CommandPayloads } from "@/lib/api/types";
import { FakeStream, feedVersion } from "@/lib/__tests__/fake-stream";
import { useCommand } from "@/lib/hooks/useCommand";
import { readyData, useRead } from "@/lib/hooks/useRead";
import { clearReadCache, readResult, subscribe, type ReadDescriptor } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";

const wrapper = ({ children }: { children: ReactNode }) => createElement(AuthProvider, null, children);
const read = userSettingsRead() as unknown as ReadDescriptor<unknown>;
const settle = () => act(() => vi.advanceTimersByTimeAsync(0));

describe("a write answers where its reads stand", () => {
  // The account's version each read answer stands on, and each body it serves.
  let at: number;
  let body: number;
  let filed: number;
  // A read the test holds in flight, released by hand.
  let hold: Promise<void> | null;

  beforeEach(() => {
    FakeStream.opened = [];
    vi.stubGlobal("EventSource", FakeStream);
    vi.useFakeTimers();
    clearReadCache(true);
    at = 1;
    body = 1;
    filed = 0;
    hold = null;
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/commands/start-run")) {
        filed += 1;
        at += 1;
        body += 1;
        const produced = { account: feedVersion(at) };
        return Response.json({}, { headers: { [FEED_VERSIONS_HEADER]: JSON.stringify(produced) } });
      }
      const stood = { account: feedVersion(at) };
      const served = body;
      if (hold) await hold;
      return Response.json({ served }, { headers: { [FEED_VERSIONS_HEADER]: JSON.stringify(stood) } });
    });
  });
  afterEach(() => {
    cleanup();
    clearReadCache(true);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("files one start-run on a double press: the second meets the first still standing up its reads", async () => {
    const unwatch = subscribe(() => read, () => {});
    await settle();
    let release = () => {};
    const view = renderHook(() => useCommand<"start">("start", "c1"), { wrapper });
    await settle();
    hold = new Promise((resolve) => (release = resolve));
    const press = () =>
      view.result.current.run("start", () =>
        commands["start-run"]({} as CommandPayloads["start-run"]),
      );
    let first!: ReturnType<typeof press>;
    act(() => {
      first = press();
    });
    await settle();
    // The ack names the version its write produced; the settings read stood before it, so it re-asks.
    expect(filed).toBe(1);
    expect(view.result.current.pending).toBe("start");
    let second!: ReturnType<typeof press>;
    act(() => {
      second = press();
    });
    await settle();
    expect(await second).toEqual({ ok: false, failure: null });
    expect(filed).toBe(1);
    release();
    await settle();
    expect((await first).ok).toBe(true);
    expect(view.result.current.pending).toBeNull();
    // Applied renders the read that stands on the write: never before it.
    expect(readResult(read)).toEqual({ status: "ready", data: { served: 2 } });
    unwatch();
  });

  it("re-arms a press only where the read it gates on stands past the write it filed", async () => {
    // Each render's press state beside the body its gating read shows.
    const seen: [string | null, unknown][] = [];
    const view = renderHook(
      () => {
        const command = useCommand<"start">("start", "c1");
        seen.push([command.pending, readyData(useRead(read))]);
        return command;
      },
      { wrapper },
    );
    await settle();
    let release = () => {};
    hold = new Promise((resolve) => (release = resolve));
    const press = () =>
      view.result.current.run("start", () =>
        commands["start-run"]({} as CommandPayloads["start-run"]),
      );
    let first!: ReturnType<typeof press>;
    act(() => {
      first = press();
    });
    await settle();
    release();
    await settle();
    expect((await first).ok).toBe(true);
    // Every render after the press that shows it pressable shows the read past the write.
    const rearmed = seen.slice(seen.findIndex(([pending]) => pending === "start"));
    const shown = rearmed.filter(([pending]) => pending === null).map(([, body]) => body);
    expect(shown.length).toBeGreaterThan(0);
    expect(shown.every((body) => JSON.stringify(body) === JSON.stringify({ served: 2 }))).toBe(true);
    // The press made once the ack settled is a fresh act on the state it shows, and files.
    let next!: ReturnType<typeof press>;
    act(() => {
      next = press();
    });
    await settle();
    expect((await next).ok).toBe(true);
    expect(filed).toBe(2);
  });
});
