import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeStream } from "@/lib/__tests__/fake-stream";
import { DEFAULT_VIEWS, NO_KEYS, formatAddress } from "@/lib/address";
import { commands } from "@/lib/api";
import { clearIncidents } from "@/lib/diagnostics";
import { clearReadCache } from "@/lib/read-cache";
import { armedCaps, dash, subtreeCampaign, subtreeCycle } from "@/lib/test-fixtures";
import { InWorkspace } from "@/lib/test-utils/in-workspace";
import { selectedCandidateOf } from "@/lib/types";
import { ApplyScenarioPanel } from "@/components/candidates/ApplyScenarioPanel";
import { RunLimitsControl } from "@/components/shell/remote/RunLimitsControl";
import { BenchGradeAction } from "../BenchGradeAction";
import { VerifyAction } from "../VerifyAction";

// The viewed cycle, the link the test moves: A and B are two cycles, each offering every press.
let path = [{ campaignId: "A", cycleId: "root" }];
const view = (campaignId: string) =>
  act(() => {
    path = [{ campaignId, cycleId: "root" }];
    window.history.replaceState(null, "", formatAddress({ subject: { path }, views: DEFAULT_VIEWS, keys: NO_KEYS }));
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

// Each campaign's served tree: its root cycle offers a verify and a held-out grade.
const tree = (campaignId: string) =>
  subtreeCampaign({
    address: campaignId,
    children: [
      subtreeCycle(
        { address: `${campaignId}::root` },
        {
          verify: { can_run: true, refusal: null },
          // The fields a press reads; the rest of a `BenchScore` no press draws.
          bench: { status: { offered: true, can_run: true, refusal: null } } as never,
        },
      ),
    ],
  });

// A command whose answer the test holds: unsettled, the press stays in flight.
function held<T>(): Promise<T> {
  return new Promise<T>(() => {});
}

// Pressed on A, then the view moves to B: B paints none of A's state.
async function moved(ui: () => ReactElement, press: () => void): Promise<void> {
  view("A");
  const { rerender } = render(<InWorkspace>{ui()}</InWorkspace>);
  await screen.findAllByRole("button");
  await act(async () => press());
  view("B");
  rerender(<InWorkspace>{ui()}</InWorkspace>);
  await screen.findAllByRole("button");
}

describe("a press's state is the subject's it was pressed on", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeStream);
    vi.stubGlobal("fetch", async (url: string) => {
      const asked = new URL(url, "http://x").pathname;
      if (asked.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (asked.endsWith("/dashboard")) return Response.json(dash());
      const campaign = /\/hierarchy\/([AB])$/.exec(asked)?.[1];
      return campaign ? Response.json(tree(campaign)) : new Response(null, { status: 404 });
    });
    clearReadCache(true);
  });
  afterEach(() => {
    cleanup();
    clearIncidents();
    clearReadCache(false);
    window.history.replaceState(null, "", window.location.pathname);
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const candidate = selectedCandidateOf("root", 1, "c1", "C1");
  const verify = () => <VerifyAction candidate={candidate} path={path} />;

  it("VerifyAction: A's saturated note paints nothing on B's same-keyed candidate", async () => {
    vi.spyOn(commands, "verify-candidate").mockResolvedValue({
      kind: "verify_saturated",
      dataset_name: "ds",
      already_measured: 3,
    });
    await moved(verify, () => fireEvent.click(screen.getByRole("button", { name: "Verify C1" })));
    expect(screen.queryByText(/already measured/)).toBeNull();
  });

  it("VerifyAction: A's Stop paints nothing on B", async () => {
    vi.spyOn(commands, "verify-candidate").mockReturnValue(held());
    await moved(verify, () => fireEvent.click(screen.getByRole("button", { name: "Verify C1" })));
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("BenchGradeAction: A's Stop paints nothing on B", async () => {
    vi.spyOn(commands, "held-out").mockReturnValue(held());
    await moved(
      () => <BenchGradeAction />,
      () => fireEvent.click(screen.getByRole("button", { name: "Grade held-out" })),
    );
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("ApplyScenarioPanel: A's fork paints nothing on B, which offers its own", async () => {
    vi.spyOn(commands, "fork-cycle").mockResolvedValue({} as never);
    const panel = () => (
      <ApplyScenarioPanel path={path} isLive={false} criterion="mean" divergentRound={null} nextRound={2} />
    );
    await moved(panel, () => fireEvent.click(screen.getByRole("button", { name: "Apply from round 2" })));
    expect(screen.queryByText(/Forked at round/)).toBeNull();
    expect(screen.getByRole("button", { name: "Apply from round 2" })).toBeTruthy();
  });

  it("RunLimitsControl: A's applied note paints nothing on B", async () => {
    vi.spyOn(commands, "set-limits").mockResolvedValue({} as never);
    const { caps } = armedCaps([
      { axis: "usd", asked: 10, armed: 10, used: 0, meter: "bill", count: null, fill: 0, clamped_by: null },
    ]);
    const limits = () => <RunLimitsControl caps={caps} overshoot={false} overshot={null} />;
    await moved(limits, () => fireEvent.click(screen.getByRole("button", { name: /^Lift the/ })));
    expect(screen.queryByText(/^Applied/)).toBeNull();
  });
});
