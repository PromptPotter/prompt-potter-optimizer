import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_VIEWS, formatAddress } from "@/lib/address";
import { ThreadProvider } from "@/lib/chat/thread";
import { IngestFlowProvider } from "@/lib/state/ingest-flow";
import { clearReadCache } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";
import { ViewMemoryProvider } from "@/lib/state/view-memory";
import { WorkspaceProvider } from "@/lib/workspace";
import { CriticalAlertBanner } from "../CriticalAlertBanner";

const GONE = "This campaign no longer exists";
const RETURNING = /Returning to the active run/;

// The gone row's detail, which its label's hint opens on focus.
async function goneDetail(): Promise<string | null> {
  fireEvent.focus((await screen.findByText(GONE)).parentElement!);
  return screen.getByRole("note").textContent;
}

// Signed in; a route named here answers its body, any other 404s.
function serve(routes: Record<string, unknown>): void {
  vi.stubGlobal("fetch", async (url: string) => {
    const path = new URL(url, "http://x").pathname;
    if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
    const hit = Object.keys(routes).find((route) => path.endsWith(route));
    return hit === undefined ? new Response(null, { status: 404 }) : Response.json(routes[hit]);
  });
}

function view() {
  return render(
    <AuthProvider>
      <ViewMemoryProvider>
        <WorkspaceProvider>
          <ThreadProvider>
            <IngestFlowProvider>
              <CriticalAlertBanner />
            </IngestFlowProvider>
          </ThreadProvider>
        </WorkspaceProvider>
      </ViewMemoryProvider>
    </AuthProvider>,
  );
}

describe("CriticalAlertBanner — a 404 names a gone campaign on the viewed cycle alone", () => {
  beforeEach(() => clearReadCache(true));
  afterEach(() => window.history.replaceState(null, "", window.location.pathname));

  it("says a 404 on the listing or the pointer, with no cycle viewed, as its own failure", async () => {
    serve({ "/backends": [] });
    view();
    expect(await screen.findByText("What this acts on no longer exists.")).toBeTruthy();
    expect(screen.queryByText(GONE)).toBeNull();
  });

  it("says a 404 on the followed active run's dashboard is a gone campaign it does not leave", async () => {
    serve({
      "/backends": [],
      "/sessions/active": { tenant_id: "t", campaign_id: "c", cycle_id: "y" },
      "/hierarchy": { entries: [] },
    });
    view();
    expect(await goneDetail()).not.toMatch(RETURNING);
  });

  it("says a 404 on a pinned cycle's dashboard is a gone campaign it returns from", async () => {
    window.history.replaceState(
      null,
      "",
      formatAddress({ subject: { path: [{ campaignId: "c", cycleId: "y" }] }, views: DEFAULT_VIEWS }),
    );
    serve({ "/backends": [], "/hierarchy": { entries: [] } });
    view();
    expect(await goneDetail()).toMatch(RETURNING);
  });
});
