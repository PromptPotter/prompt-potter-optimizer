import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { commands } from "@/lib/api";
import { clearIncidents } from "@/lib/diagnostics";
import { AuthProvider } from "@/lib/state/auth";
import { armedCaps } from "@/lib/test-fixtures";
import { selectedCandidateOf } from "@/lib/types";
import { ApplyScenarioPanel } from "@/components/candidates/ApplyScenarioPanel";
import { RunLimitsControl } from "@/components/shell/remote/RunLimitsControl";
import { BenchGradeAction } from "../BenchGradeAction";
import { VerifyAction } from "../VerifyAction";

// The viewed cycle, moved by the test: A and B are two cycles, each offering every press.
const at = (campaignId: string) => {
  const path = [{ campaignId, cycleId: "root" }];
  return { path, address: `${campaignId}::root`, root: { campaignId }, leaf: { cycleId: "root" } };
};
let viewed = at("A");
vi.mock("@/lib/state/workspace", () => ({ useWorkspace: () => ({ viewed }) }));
vi.mock("@/lib/state/lineage", () => ({
  useViewedCycle: () => ({
    facts: {
      verify: { can_run: true, refusal: null },
      bench: { status: { offered: true, can_run: true, refusal: null } },
    },
  }),
}));
vi.mock("@/lib/hooks/useVerify", () => ({
  useVerify: () => ({ readings: new Map(), passes: new Map(), unit: null }),
}));

// A command whose answer the test holds: unsettled, the press stays in flight.
function held<T>(): Promise<T> {
  return new Promise<T>(() => {});
}

// Pressed on A, then the view moves to B: B paints none of A's state.
async function moved(ui: () => ReactElement, press: () => void): Promise<void> {
  const { rerender } = render(<AuthProvider>{ui()}</AuthProvider>);
  await act(async () => press());
  viewed = at("B");
  rerender(<AuthProvider>{ui()}</AuthProvider>);
}

describe("a press's state is the subject's it was pressed on", () => {
  beforeEach(() => {
    viewed = at("A");
    vi.stubGlobal("fetch", () => Promise.reject(new Error("offline")));
  });
  afterEach(() => {
    cleanup();
    clearIncidents();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const candidate = selectedCandidateOf("root", 1, "c1", "C1");
  const verify = () => <VerifyAction candidate={candidate} path={viewed.path} />;

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
      <ApplyScenarioPanel path={viewed.path} isLive={false} criterion="mean" divergentRound={null} nextRound={2} />
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
