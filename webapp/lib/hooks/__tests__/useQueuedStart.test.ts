// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FEED_VERSIONS_HEADER,
  type CommandReading,
  type CycleHop,
  type DraftCampaignWire,
  type DraftReopenResponse,
} from "@/lib/api/types";
import { ThreadProvider } from "@/lib/state/chat-thread";
import { useIngestFlow } from "@/lib/hooks/useIngestFlow";
import { useQueuedStart } from "@/lib/hooks/useQueuedStart";
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

describe("a queued Start follows its whole command reading on the feed", () => {
  const minted: CycleHop = { campaign_id: "camp_new", cycle_id: "cyc_1" };
  let served: Pick<CommandReading, "status" | "said"> & { hop: CycleHop | null };
  let asked: number;
  // The account's store at the version the server reads it at, which the command's answer stood on.
  let store: string;

  const reading = (): CommandReading => ({
    command_id: "cmd_1",
    kind: "start-checkin",
    subject: "draft:d1",
    status: served.status,
    said: served.said,
    created_at: "2026-11-11T00:00:00Z",
    last_updated_at: "2026-11-11T00:00:00Z",
    launches: [{ job_id: "job_1", hop: served.hop, what: "the check-in's run" }],
  });

  const followed = async () => {
    const view = renderHook(() => useQueuedStart("cmd_1"), { wrapper });
    await settle();
    expect(asked).toBe(1);
    expect(view.result.current).toEqual({ stage: "waiting" });
    const [feed] = FakeFeed.opened;
    if (!feed) throw new Error("no feed opened");
    act(() => feed.frame("store:", store));
    // Nothing asks on a clock: a queued launch waits on the feed alone.
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(asked).toBe(1);
    return { view, feed };
  };

  beforeEach(() => {
    FakeFeed.opened = [];
    vi.stubGlobal("EventSource", FakeFeed);
    clearReadCache(true);
    vi.useFakeTimers();
    served = { status: "working", said: "the check-in's run: queued", hop: null };
    asked = 0;
    store = "v1";
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/commands/cmd_1")) {
        asked += 1;
        return Response.json(reading(), {
          headers: { [FEED_VERSIONS_HEADER]: JSON.stringify({ "store:": store }) },
        });
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
    const { view, feed } = await followed();
    // A slot frees: the job names its cycle, and the minted run's appends move the store.
    served = { status: "working", said: "the check-in's run: running", hop: minted };
    store = "v2";
    act(() => feed.frame("store:", store));
    await settle();
    expect(asked).toBe(2);
    expect(view.result.current).toEqual({ stage: "minted", hop: minted });
  });

  it("ends unminted, in the served words, when its slot refuses it or it is withdrawn", async () => {
    const endings: Pick<CommandReading, "status" | "said">[] = [
      { status: "failed", said: "the check-in's run: the backend did not answer its probe" },
      { status: "cancelled", said: "the check-in's run: withdrawn" },
    ];
    for (const [at, ending] of endings.entries()) {
      cleanup();
      clearReadCache(true);
      FakeFeed.opened = [];
      served = { status: "working", said: "the check-in's run: queued", hop: null };
      asked = 0;
      store = "v1";
      const { view, feed } = await followed();
      served = { ...ending, hop: null };
      store = `end${at}`;
      act(() => feed.frame("store:", store));
      await settle();
      expect(asked).toBe(2);
      expect(view.result.current, ending.status).toEqual({ stage: "ended", said: ending.said });
    }
  });
});

describe("a reopened queued Start follows the Start's own command, as the tab that tapped it does", () => {
  const minted: CycleHop = { campaign_id: "camp_new", cycle_id: "cyc_1" };
  const draft = {
    draft_id: "d1",
    slug: "rows",
    n_samples: 3,
    raw_task_description: "label each row",
    candidate_library_size: 0,
  } as DraftCampaignWire;
  let command: Pick<CommandReading, "status" | "said"> & { hop: CycleHop | null };
  // The Start holding the draft as its reopen serves it; past the mint the draft is closed (404).
  let held: DraftReopenResponse["start"] | "closed";
  let store: string;
  const stood = () => ({ [FEED_VERSIONS_HEADER]: JSON.stringify({ "store:": store }) });
  const flowWrapper = ({ children }: { children: ReactNode }) =>
    createElement(AuthProvider, null, createElement(ThreadProvider, null, children));

  // A reload onto the draft: nothing of the Start lives in this tab but what the reopen serves.
  const reopened = async (onMint: () => void) => {
    const view = renderHook(
      () =>
        useIngestFlow({
          subject: "d1",
          onMint,
          onDraft: () => {},
          onDiscarded: () => {},
          onCompose: () => {},
        }),
      { wrapper: flowWrapper },
    );
    for (let i = 0; i < 4; i += 1) await settle();
    expect(view.result.current.phase).toEqual({ stage: "queued" });
    const [feed] = FakeFeed.opened;
    if (!feed) throw new Error("no feed opened");
    return { view, feed };
  };

  beforeEach(() => {
    FakeFeed.opened = [];
    vi.stubGlobal("EventSource", FakeFeed);
    clearReadCache(true);
    vi.useFakeTimers();
    command = { status: "working", said: "the check-in's run: queued", hop: null };
    held = { stage: "queued", command_id: "cmd_1" };
    store = "v1";
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/drafts/d1")) {
        if (held === "closed") {
          const gone = { code: "not_found", message: "no draft d1", details: {}, error_id: "e1" };
          return Response.json(gone, { status: 404, headers: stood() });
        }
        const body: DraftReopenResponse = {
          draft,
          resolution: { last_resolution: null, raised: [], degraded_cause: null },
          resolution_pending: false,
          start: held,
        };
        return Response.json(body, { headers: stood() });
      }
      if (path.endsWith("/commands/cmd_1")) {
        const body: CommandReading = {
          command_id: "cmd_1",
          kind: "start-checkin",
          subject: "draft:d1",
          status: command.status,
          said: command.said,
          created_at: "2026-11-11T00:00:00Z",
          last_updated_at: "2026-11-11T00:00:00Z",
          launches: [{ job_id: "job_1", hop: command.hop, what: "the check-in's run" }],
        };
        return Response.json(body, { headers: stood() });
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

  it("moves onto the campaign its slot minted", async () => {
    const onMint = vi.fn();
    const { view, feed } = await reopened(onMint);
    command = { status: "working", said: "the check-in's run: running", hop: minted };
    held = "closed";
    store = "v2";
    act(() => feed.frame("store:", store));
    for (let i = 0; i < 4; i += 1) await settle();
    expect(onMint).toHaveBeenCalledWith({ campaignId: "camp_new", cycleId: "cyc_1" });
    expect(view.result.current.phase).toEqual({ stage: "idle" });
  });

  it("lands back in review, the refusal beside Start, when its slot refuses it", async () => {
    const onMint = vi.fn();
    const { view, feed } = await reopened(onMint);
    const said = "the check-in's run: the backend did not answer its probe";
    command = { status: "failed", said, hop: null };
    held = null;
    store = "v2";
    act(() => feed.frame("store:", store));
    for (let i = 0; i < 4; i += 1) await settle();
    expect(onMint).not.toHaveBeenCalled();
    expect(view.result.current.phase.stage).toBe("ready");
    expect(view.result.current.failure?.message).toBe(said);
  });
});
