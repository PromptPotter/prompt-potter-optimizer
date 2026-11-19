// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  FEED_VERSIONS_HEADER,
  type DraftCampaignWire,
  type DraftReopenResponse,
} from "@/lib/api/types";
import { poolToggle } from "@/components/pipeline/NodeKnobsPanel";
import { FakeStream, feedVersion } from "@/lib/__tests__/fake-stream";
import { draftRead } from "@/lib/api/reads";
import { useIngestFlow, type IngestFlow } from "@/lib/hooks/useIngestFlow";
import { clearReadCache, reaskRead } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";
import { ThreadProvider } from "@/lib/state/chat-thread";

const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(AuthProvider, null, createElement(ThreadProvider, null, children));
const settle = async () => {
  for (let i = 0; i < 4; i += 1) await act(() => vi.advanceTimersByTimeAsync(0));
};

describe("a check-in's draft writes build on the served draft, in the order they were pressed", () => {
  // The server's draft: its pool's `active`, at the store version each answer stands on.
  let active: string[];
  let store: number;
  // A draft read the test holds in flight, released by hand.
  let hold: Promise<void> | null;
  let release: () => void;

  const draft = (): DraftCampaignWire =>
    ({
      draft_id: "d1",
      slug: "rows",
      n_samples: 3,
      raw_task_description: "label each row",
      answer_terms_size: 0,
      readiness: { complete: true },
      pipeline: {
        optimizer_setup: { pools: [{ pool: "p", initializer: "init", active, packs: [] }] },
      },
    }) as unknown as DraftCampaignWire;
  const stood = () => ({ [FEED_VERSIONS_HEADER]: JSON.stringify({ "store:": feedVersion(store) }) });

  // Every render's flow, in order: what the check-in drew at each.
  let flows: IngestFlow[];
  const held = (d: DraftCampaignWire): string[] => [...(d.pipeline.optimizer_setup.pools[0]?.active ?? [])];

  const reviewed = async () => {
    const view = renderHook(
      () => {
        const flow = useIngestFlow({
          subject: "d1",
          onMint: () => {},
          onDraft: () => {},
          onDiscarded: () => {},
          onCompose: () => {},
        });
        flows.push(flow);
        return flow;
      },
      { wrapper },
    );
    await settle();
    expect(view.result.current.phase.stage).toBe("ready");
    return view;
  };
  const shown = (view: Awaited<ReturnType<typeof reviewed>>): string[] => {
    const { phase } = view.result.current;
    if (phase.stage !== "ready") throw new Error(`not in review: ${phase.stage}`);
    return held(phase.draft);
  };

  beforeEach(() => {
    FakeStream.opened = [];
    vi.stubGlobal("EventSource", FakeStream);
    clearReadCache(true);
    vi.useFakeTimers();
    active = ["*"];
    flows = [];
    store = 1;
    hold = null;
    release = () => {};
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/drafts/d1")) {
        // Read as the store stood when it was asked: a held answer lands after a later write.
        const asked = draft();
        const at = stood();
        if (hold) await hold;
        const body: DraftReopenResponse = {
          draft: asked,
          resolution: { last_resolution: null, raised: [], degraded_cause: null },
          resolution_pending: false,
          start: null,
        };
        return Response.json(body, { headers: at });
      }
      if (path.endsWith("/commands/edit-draft-campaign")) {
        const sent = JSON.parse(String(init?.body)) as {
          patch: { config?: { optimization?: { nodes?: Record<string, { config: { active: string[] } }> } } };
        };
        const next = sent.patch.config?.optimization?.nodes?.init?.config.active;
        if (next) active = next;
        store += 1;
        return Response.json(draft(), { headers: stood() });
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

  it("lands both of two chip presses made inside one write's ack", async () => {
    const view = await reviewed();
    hold = new Promise((resolve) => (release = resolve));
    act(() => view.result.current.applyPatch(poolToggle("p", "active", "a")));
    await settle();
    // The first write's ack waits on the draft read that stands on it; the second press meets it.
    expect(active).toEqual(["*", "a"]);
    expect(shown(view)).toEqual(["*"]);
    act(() => view.result.current.applyPatch(poolToggle("p", "active", "b")));
    await settle();
    hold = null;
    release();
    await settle();
    expect(active).toEqual(["*", "a", "b"]);
    expect(shown(view)).toEqual(["*", "a", "b"]);
  });

  it("never shows the draft as it stood before an edit once the edit has shown", async () => {
    await reviewed();
    // A draft read asked before the write, answered after the write's answer.
    hold = new Promise((resolve) => (release = resolve));
    act(() => reaskRead(draftRead("d1").id));
    await settle();
    const edit = { config: { optimization: { nodes: { init: { config: { active: ["*", "a"] } } } } } };
    act(() => flows.at(-1)?.applyPatch(edit));
    await settle();
    expect(active).toEqual(["*", "a"]);
    hold = null;
    release();
    await settle();
    const drawn = flows.map((flow) => (flow.phase.stage === "ready" ? held(flow.phase.draft) : null));
    const from = drawn.findIndex((d) => d?.includes("a"));
    expect(from).toBeGreaterThan(0);
    expect(drawn.slice(from)).not.toContainEqual(["*"]);
  });
});
