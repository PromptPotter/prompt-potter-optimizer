// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeStream } from "@/lib/__tests__/fake-stream";
import { DEFAULT_VIEWS, NO_KEYS, formatAddress } from "@/lib/address";
import { commands } from "@/lib/api";
import { useCampaignForest } from "@/lib/hooks/useCampaignForest";
import { clearReadCache } from "@/lib/read-cache";
import { subtreeCampaign } from "@/lib/test-fixtures";
import { InWorkspace as wrapper } from "@/lib/test-utils/in-workspace";

const useForestOf = ({ campaignId }: { campaignId: string }) =>
  useCampaignForest({ campaignId, cycleId: "c0", path: null, displayMetric: "fitness" });

describe("useCampaignForest — the lineage cleanup is the campaign's it was pressed on", () => {
  beforeEach(() => {
    // The viewed campaign A, whose served tree holds two deletable stubs.
    const path = [{ campaignId: "A", cycleId: "c0" }];
    window.history.replaceState(null, "", formatAddress({ subject: { path }, views: DEFAULT_VIEWS, keys: NO_KEYS }));
    vi.stubGlobal("EventSource", FakeStream);
    vi.stubGlobal("fetch", async (url: string) => {
      const asked = new URL(url, "http://x").pathname;
      if (asked.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (!asked.endsWith("/hierarchy/A")) return new Response(null, { status: 404 });
      const tree = subtreeCampaign({ address: "A" });
      return Response.json({ ...tree, facts: { ...tree.facts, deletable_stubs: 2 } });
    });
    vi.spyOn(commands, "cleanup-empty-cycles").mockResolvedValue({} as never);
    clearReadCache(true);
  });
  afterEach(() => {
    cleanup();
    clearReadCache(false);
    window.history.replaceState(null, "", window.location.pathname);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("an ack on A reads false on B, and true again on A", async () => {
    const { result, rerender } = renderHook(useForestOf, {
      initialProps: { campaignId: "A" },
      wrapper,
    });
    await waitFor(() => expect(result.current.cleanup.stubCount).toBe(2));
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
    const { result, rerender } = renderHook(useForestOf, {
      initialProps: { campaignId: "A" },
      wrapper,
    });
    act(() => result.current.cleanup.request());
    rerender({ campaignId: "B" });
    expect(result.current.cleanup.open).toBe(false);
  });
});
