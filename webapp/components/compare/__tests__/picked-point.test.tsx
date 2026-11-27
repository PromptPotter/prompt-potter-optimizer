import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EMPTY_LINEAGE, type CompareItem } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";
import { clearReadCache } from "@/lib/read-cache";
import { hierarchyCandidate, roundOfArms } from "@/lib/test-fixtures";
import { InWorkspace } from "@/lib/test-utils/in-workspace";
import { usePickedPoint } from "../channel-hooks";
import { NO_EDITS, ScenarioEditsProvider } from "../config-edit";

const CYCLE = encodeCyclePath([{ campaignId: "c", cycleId: "y" }]);
const ARM = { round: 1, key: "k1", label: "C1.1" };
const PICKED = hierarchyCandidate(CYCLE, { id: "i1", ...ARM });
const ITEM: CompareItem = {
  subject: "cycle:c/y",
  reading: null,
  slot: null,
  headline: null,
};

// Round 1 still scoring on the picked point's own cycle: no round file, the round read serves its arms.
const SCORING = { ...roundOfArms(1, [{ ...ARM, resolved_params: { temperature: 0.3 } }]), unfiled: true };

const EDITING = { edits: NO_EDITS, setEdits: () => {} };

function wrapper({ children }: { children: ReactNode }) {
  return (
    <InWorkspace>
      <ScenarioEditsProvider value={EDITING}>{children}</ScenarioEditsProvider>
    </InWorkspace>
  );
}

describe("usePickedPoint — a point on a round still scoring", () => {
  const asked: string[] = [];
  beforeEach(() => {
    asked.length = 0;
    clearReadCache(true);
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      asked.push(path);
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/hierarchy")) return Response.json({ entries: [], docked: [] });
      if (path.endsWith("/rounds/1")) return Response.json(SCORING);
      return new Response(null, { status: 404 });
    });
  });

  it("reads its config and reading off the round read alone, never the channel's dashboard", async () => {
    const { result } = renderHook(() => usePickedPoint(ITEM, PICKED, EMPTY_LINEAGE), { wrapper });
    await waitFor(() => expect(result.current.cfg?.config).toEqual({ temperature: 0.3 }));
    expect(result.current.reading?.arm).toEqual(ARM);
    expect(result.current.unfiled).toBe(true);
    expect(asked.some((p) => p.endsWith("/dashboard"))).toBe(false);
  });
});
