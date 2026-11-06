import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EMPTY_LINEAGE, type CompareItem } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";
import { clearReadCache } from "@/lib/read-cache";
import { SelectionProvider } from "@/lib/SelectionContext";
import { ThreadProvider } from "@/lib/chat/thread";
import { IngestFlowProvider } from "@/lib/ingest-flow";
import { AuthProvider } from "@/lib/state/auth";
import { ViewMemoryProvider } from "@/lib/state/view-memory";
import { WorkspaceProvider } from "@/lib/workspace";
import {
  armReading,
  currentRound,
  dash,
  hierarchyCandidate,
  liveRow,
  producerReading,
} from "@/lib/test-fixtures";
import { usePickedPoint } from "../channel-hooks";
import { NO_EDITS, ScenarioEditsProvider } from "../config-edit";

const COURSE = encodeCyclePath([{ campaignId: "c", cycleId: "y" }]);
const ARM = { round: 1, key: "k1", label: "C1.1" };
const PICKED = hierarchyCandidate(COURSE, { id: "i1", ...ARM });
const ITEM: CompareItem = {
  channel: { rootCampaignId: "c", subject: "course:c/y" },
  reading: null,
  slot: null,
  headline: null,
};

// Round 1 still scoring on the picked point's own cycle: no round file, its row on the dashboard.
const SCORING = dash({
  producer: producerReading({ attached: true }),
  current_round: currentRound({
    round: 1,
    candidates: [liveRow({ reading: armReading({ arm: ARM }), resolved_params: { temperature: 0.3 } })],
  }),
});

const EDITING = { edits: NO_EDITS, setEdits: () => {} };

function wrapper({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <ViewMemoryProvider>
        <WorkspaceProvider>
          <ThreadProvider>
            <IngestFlowProvider>
              <SelectionProvider cycleId={null}>
                <ScenarioEditsProvider value={EDITING}>{children}</ScenarioEditsProvider>
              </SelectionProvider>
            </IngestFlowProvider>
          </ThreadProvider>
        </WorkspaceProvider>
      </ViewMemoryProvider>
    </AuthProvider>
  );
}

describe("usePickedPoint — a point on a round still scoring", () => {
  beforeEach(() => {
    clearReadCache(true);
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/hierarchy")) return Response.json({ entries: [] });
      if (path.endsWith("/dashboard")) return Response.json(SCORING);
      return new Response(null, { status: 404 });
    });
  });

  it("reads its config and reading off the channel's own cycle, which this tab streams", async () => {
    const { result } = renderHook(() => usePickedPoint(ITEM, PICKED, EMPTY_LINEAGE), { wrapper });
    await waitFor(() => expect(result.current.cfg?.config).toEqual({ temperature: 0.3 }));
    expect(result.current.reading?.arm).toEqual(ARM);
    expect(result.current.roundRead.status).toBe("idle");
  });
});
