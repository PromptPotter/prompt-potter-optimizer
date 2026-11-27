import { cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeStream } from "@/lib/__tests__/fake-stream";
import { DEFAULT_VIEWS, NO_KEYS, formatAddress } from "@/lib/address";
import type { ActivityDecision, Filable } from "@/lib/api/types";
import { ApplyScenarioPanel } from "@/components/candidates/ApplyScenarioPanel";
import { LiveSegment } from "@/components/chat/LiveSegment";
import { SteerForkAction } from "@/components/searchpoint/SteerForkAction";
import { VerifyAction } from "@/components/searchpoint/VerifyAction";
import { useCampaignForest } from "@/lib/hooks/useCampaignForest";
import { clearReadCache } from "@/lib/read-cache";
import { ThreadProvider } from "@/lib/state/chat-thread";
import { IngestFlowProvider } from "@/lib/state/ingest-flow";
import { useWorkspace } from "@/lib/state/workspace";
import { OWNER_FILABLE, dash, hierarchyState, subtreeCampaign, subtreeCycle } from "@/lib/test-fixtures";
import { InWorkspace } from "@/lib/test-utils/in-workspace";
import { selectedCandidateOf } from "@/lib/types";
import { CriticalAlertBanner } from "../CriticalAlertBanner";
import { RemoteControl } from "../remote/RemoteControl";

const PATH = [{ campaignId: "camp-exhibit", cycleId: "cycle_exhibit" }];
const OWN = [{ campaignId: "camp-own", cycleId: "cycle_own" }];
const READ_ONLY = "You read this run; acting on it is not yours.";

// A held round's decision as the server words it for its reader (`activity.py::ActivityAt.read_by`).
function decision(answers: boolean): ActivityDecision {
  return {
    title: answers ? "Round 2 holds for your arms" : "Round 2 holds for its arms",
    lead: "Round 2 measures nothing until it is answered with its arms.",
    facts: [],
    reasons: [],
    actions: answers
      ? [{ label: "Measure the drafts", variant: "primary", kind: "submit-arms", payload: { keep: [0] } }]
      : [],
  };
}

// A campaign's tree as the server answers a reader filing *filable*: its one cycle's verify
// joined to that reader, and two stubs to clean up where it may.
function tree(campaignId: string, cycleId: string, filable: Filable[]) {
  const owner = filable.length > 0;
  const admission = owner
    ? { offers: "pause" as const, refusal: null, payload: {}, stop_acts: [] }
    : {
        offers: null,
        refusal: { code: "read_only", message: READ_ONLY, details: {}, next_acts: [], notes: [] },
        payload: {},
        stop_acts: [],
      };
  const served = subtreeCampaign({
    address: campaignId,
    state: hierarchyState({ admission }),
    children: [
      subtreeCycle(
        { address: `${campaignId}::${cycleId}`, state: hierarchyState({ admission }) },
        { verify: owner ? { can_run: true, refusal: null } : { can_run: false, refusal: "read_only" } },
      ),
    ],
  });
  return {
    admission,
    tree: { ...served, facts: { ...served.facts, deletable_stubs: owner ? 2 : 0, filable } },
  };
}

// The exhibit as the server answers a reader filing *filable*: its pointer, a held round gone
// critical with every loop act its reader's, and the caller's own campaign beside it.
function serve(filable: Filable[]): void {
  const exhibit = tree("camp-exhibit", "cycle_exhibit", filable);
  const routes: Record<string, unknown> = {
    "/backends": [],
    "/sessions/active": { hop: { campaign_id: PATH[0]!.campaignId, cycle_id: PATH[0]!.cycleId }, said: null },
    "/hierarchy": { entries: [], docked: [] },
    "/hierarchy/camp-exhibit": exhibit.tree,
    "/hierarchy/camp-own": tree("camp-own", "cycle_own", OWNER_FILABLE).tree,
    "/dashboard": dash({
      run_phase: "gate",
      admission: exhibit.admission,
      skip_refusal: filable.length > 0 ? "" : READ_ONLY,
      said: { key: "round_critical", tone: "danger", label: "Round 2 is critical", value: null, sub: null, detail: null },
    }),
  };
  vi.stubGlobal("EventSource", FakeStream);
  vi.stubGlobal("fetch", async (url: string) => {
    const path = new URL(url, "http://x").pathname;
    if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
    const hit = Object.keys(routes).find((route) => path.endsWith(route));
    return hit === undefined ? new Response(null, { status: 404 }) : Response.json(routes[hit]);
  });
}

// A click that pins the followed path, as a candidate pick, a forest pick or a scrub does.
function Pin() {
  const { navigate } = useWorkspace();
  return (
    <button type="button" onClick={() => navigate({ path: PATH })}>
      pin
    </button>
  );
}

function view(answers: boolean) {
  return render(
    <InWorkspace>
      <ThreadProvider>
        <IngestFlowProvider>
          <Pin />
          <CriticalAlertBanner />
          <RemoteControl />
          <LiveSegment notices={[]} status={null} listening={false} decision={decision(answers)} />
        </IngestFlowProvider>
      </ThreadProvider>
    </InWorkspace>,
  );
}

const ACTS = [/^Pause/, /Skip/, /^Measure the drafts$/, /Set caps/, /Lift the/];

describe("exhibit acts — a reader is offered only what its served read offers it, pinned or followed", () => {
  beforeEach(() => clearReadCache(true));
  afterEach(() => {
    cleanup();
    clearReadCache(false);
    window.history.replaceState(null, "", window.location.pathname);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("the exhibit's follower, its path pinned, reads the held round and is offered no act", async () => {
    serve([]);
    view(false);
    fireEvent.click(screen.getByRole("button", { name: "pin" }));
    expect(await screen.findByText("Round 2 is critical")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Job status and configuration" }));
    expect(await screen.findByText("Samples in flight")).toBeTruthy();
    for (const act of ACTS) expect(screen.queryByRole("button", { name: act })).toBeNull();
    expect(screen.queryByRole("button", { name: /Pause run|Resume run|Start run/ })).toBeNull();
    const forest = renderHook(
      () => useCampaignForest({ campaignId: "camp-exhibit", cycleId: "cycle_exhibit", path: PATH, displayMetric: "fitness" }),
      { wrapper: InWorkspace },
    );
    await waitFor(() => expect(forest.result.current.tree).not.toBeNull());
    expect(forest.result.current.cleanup.stubCount).toBe(0);
  });

  it("the owner of the same served run is offered every one of them", async () => {
    serve(OWNER_FILABLE);
    view(true);
    fireEvent.click(screen.getByRole("button", { name: "pin" }));
    expect(await screen.findByText("Round 2 is critical")).toBeTruthy();
    for (const shown of ["Pause the campaign", "Pause run", /Skip/, "Measure the drafts"]) {
      expect(await screen.findByRole("button", { name: shown })).toBeTruthy();
    }
    fireEvent.click(screen.getByRole("button", { name: "Job status and configuration" }));
    expect(await screen.findByRole("button", { name: "Set caps" })).toBeTruthy();
    const forest = renderHook(
      () => useCampaignForest({ campaignId: "camp-exhibit", cycleId: "cycle_exhibit", path: PATH, displayMetric: "fitness" }),
      { wrapper: InWorkspace },
    );
    await waitFor(() => expect(forest.result.current.cleanup.stubCount).toBe(2));
  });

  it("in compare, each channel's acts read the tree its own path came from, whichever is viewed; a readout stays", async () => {
    serve([]);
    for (const viewed of [PATH, OWN]) {
      window.history.replaceState(null, "", formatAddress({ subject: { path: viewed }, views: DEFAULT_VIEWS, keys: NO_KEYS }));
      const channels = render(
        <InWorkspace>
          {[PATH, OWN].map((path) => (
            <div key={path[0]!.campaignId} data-testid={path[0]!.campaignId}>
              <VerifyAction candidate={selectedCandidateOf(path[0]!.cycleId, 1, "c1", "C1")} path={path} />
              <SteerForkAction candidate={selectedCandidateOf(path[0]!.cycleId, 1, "c1", "C1")} path={path} />
              <ApplyScenarioPanel path={path} isLive={false} criterion="mean" divergentRound={null} nextRound={2} />
            </div>
          ))}
        </InWorkspace>,
      );
      const own = await channels.findByTestId("camp-own");
      await waitFor(() => expect(own.querySelector("button")).not.toBeNull());
      expect(own.textContent).toMatch(/Verify C1/);
      expect(own.textContent).toMatch(/Steer & fork/);
      expect(own.textContent).toMatch(/Apply from round 2/);
      const exhibit = channels.getByTestId("camp-exhibit");
      expect(exhibit.textContent).not.toMatch(/Verify C1|Steer & fork|Apply from/);
      // The scenario's readout is every reader's: only its fork is pressed by whoever files one.
      expect(exhibit.textContent).toMatch(/never parts from the record/);
      channels.unmount();
    }
  });
});
