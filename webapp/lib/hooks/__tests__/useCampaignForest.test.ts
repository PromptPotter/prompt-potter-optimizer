// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useCampaignForest } from "@/lib/hooks/useCampaignForest";

const tree = { facts: { root: "A/c0", deletable_stubs: 2 } };

vi.mock("@/lib/state/lineage", () => ({
  useViewedLineage: () => ({ tree, index: { cycles: [] } }),
}));
vi.mock("@/lib/state/view-memory", () => ({
  useViewMemory: () => ({
    viewFor: () => ({ treeFreed: false, expandedLanes: [], expandedForLane: null }),
    recordView: () => {},
  }),
}));
vi.mock("@/lib/state/candidates", () => ({ useMetrics: () => [] }));
vi.mock("@/lib/derivations", () => ({
  candidatesAtPath: () => [],
  cycleAt: () => null,
  nodeOverlays: () => ({ valueByKey: new Map(), thetaByKey: new Map() }),
  primaryMetric: () => "fitness",
}));
vi.mock("@/lib/ids", () => ({ pathOfAddress: () => [{ campaignId: "A", cycleId: "c0" }] }));
vi.mock("@/lib/api", () => ({ commands: { "cleanup-empty-cycles": async () => "ok" } }));
vi.mock("@/lib/hooks/useCommand", () => ({
  useCommand: () => ({
    failure: null,
    pending: null,
    clear: () => {},
    run: async (_kind: string, send: () => Promise<unknown>, then: () => void) => {
      await send();
      then();
    },
  }),
}));

afterEach(cleanup);

describe("useCampaignForest — the lineage cleanup is the campaign's it was pressed on", () => {
  it("an ack on A reads false on B, and true again on A", async () => {
    const { result, rerender } = renderHook(
      ({ campaignId }: { campaignId: string }) =>
        useCampaignForest({ campaignId, cycleId: "c0", path: null, displayMetric: "fitness" }),
      { initialProps: { campaignId: "A" } },
    );
    act(() => result.current.cleanup.request());
    expect(result.current.cleanup.open).toBe(true);
    await act(async () => result.current.cleanup.confirm());
    expect(result.current.cleanup.acked).toBe(true);
    expect(result.current.cleanup.open).toBe(false);

    rerender({ campaignId: "B" });
    expect(result.current.cleanup.acked).toBe(false);

    rerender({ campaignId: "A" });
    expect(result.current.cleanup.acked).toBe(true);
  });

  it("a dialog opened on A is not open on B", () => {
    const { result, rerender } = renderHook(
      ({ campaignId }: { campaignId: string }) =>
        useCampaignForest({ campaignId, cycleId: "c0", path: null, displayMetric: "fitness" }),
      { initialProps: { campaignId: "A" } },
    );
    act(() => result.current.cleanup.request());
    rerender({ campaignId: "B" });
    expect(result.current.cleanup.open).toBe(false);
  });
});
