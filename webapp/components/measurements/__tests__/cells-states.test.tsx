import type { ReactNode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PairwiseComparison } from "@/lib/api/types";
import { clearReadCache } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";
import { ViewMemoryProvider } from "@/lib/state/view-memory";
import { WorkspaceProvider } from "@/lib/workspace";
import { courseTags, pairedReading, subtreeCampaign, subtreeCourse } from "@/lib/test-fixtures";
import { SubjectFlips } from "@/components/shell/searchpoint/SubjectFlips";
import { MeasurementsPane } from "../MeasurementsPane";
import { RosterNote } from "../RosterNote";

// Signed out, so every gated read rests idle; the cells read, ungated, answers 404.
function serveNoSlice(): void {
  vi.stubGlobal("fetch", async (url: string) =>
    new Response(null, { status: url.includes("/cells?") ? 404 : 401 }),
  );
}

function view(pane: ReactNode) {
  return render(
    <AuthProvider>
      <ViewMemoryProvider>
        <WorkspaceProvider>{pane}</WorkspaceProvider>
      </ViewMemoryProvider>
    </AuthProvider>,
  );
}

describe("a cells read's answer, as its panes say it", () => {
  beforeEach(() => clearReadCache(true));

  it("says a 404 is no cells yet: answered, never loading", async () => {
    serveNoSlice();
    view(
      <MeasurementsPane
        preset={{ path: [{ campaignId: "c", cycleId: "y" }], datasetName: "ds", scope: "cycle" }}
      />,
    );
    expect(await screen.findByText("No measured cells here yet.")).toBeTruthy();
    expect(screen.queryByText("Loading…")).toBeNull();
  });

  it("says loading while the roster's read is idle: nothing answered it yet", async () => {
    serveNoSlice();
    view(<RosterNote />);
    await act(async () => {});
    expect(screen.getByText("Loading this campaign’s samples…")).toBeTruthy();
    expect(screen.queryByText("No samples on this campaign’s dataset yet.")).toBeNull();
  });

  it("says a refused read on the flips card, never a list of empty cells", async () => {
    // Signed in, following c::y on dataset ds; every cells read is refused.
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/sessions/active")) {
        return Response.json({ tenant_id: "t", campaign_id: "c", cycle_id: "y" });
      }
      if (path.endsWith("/hierarchy")) {
        const course = subtreeCourse({ address: "c::y", tags: courseTags("root", "ds") });
        return Response.json({ entries: [subtreeCampaign({ address: "c", children: [course] })] });
      }
      return new Response(null, { status: path.endsWith("/cells") ? 403 : 404 });
    });
    const hit = pairedReading(0.1, [0.05, 0.15]);
    const vsOrigin: PairwiseComparison = {
      subject_a: "candidate:c/y/a0",
      subject_b: "candidate:c/y/a1",
      reading: hit,
      hit: { ...hit, headline: { ...hit.headline!, flips: { gained: 1, lost: 0, unchanged: 9 } } },
      gained: ["s1"],
      lost: [],
    };
    view(<SubjectFlips vsOrigin={vsOrigin} partial={null} before="a0" after="a1" />);
    fireEvent.focus(screen.getByText("now right").parentElement!);
    expect(await screen.findByText(/Could not read the flipped cells\./)).toBeTruthy();
  });
});
