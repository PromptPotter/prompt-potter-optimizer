import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { clearReadCache } from "@/lib/read-cache";
import { AuthProvider } from "@/lib/state/auth";
import { ViewMemoryProvider } from "@/lib/state/view-memory";
import { WorkspaceProvider } from "@/lib/state/workspace";
import { armedCaps } from "@/lib/test-fixtures";
import { RunLimitsControl } from "../RunLimitsControl";

// A spend read against another book than its cap's, or a clamp left unsaid, misleads a spend.
describe("the finishing criteria read each cap against its own book", () => {
  beforeEach(() => {
    clearReadCache(true);
    vi.stubGlobal("fetch", async () => new Response(null, { status: 401 }));
  });

  it("meters only a capped axis, and says what the allowance clamped", () => {
    const { caps } = armedCaps([
      {
        axis: "usd",
        asked: 20,
        armed: 10,
        used: 0.45,
        meter: "bill",
        count: null,
        fill: 0.045,
        clamped_by: "allowance",
      },
      {
        axis: "tokens",
        asked: null,
        armed: null,
        used: 4500,
        meter: "bill",
        count: null,
        fill: null,
        clamped_by: null,
      },
    ]);
    render(
      <AuthProvider>
        <ViewMemoryProvider>
          <WorkspaceProvider>
            <RunLimitsControl caps={caps} overshoot={false} overshot={null} />
          </WorkspaceProvider>
        </ViewMemoryProvider>
      </AuthProvider>,
    );
    const meters = screen.getAllByRole("meter");
    expect(meters.map((m) => m.getAttribute("aria-label"))).toEqual(["Spend used"]);
    expect(meters[0]!.getAttribute("value")).toBe("0.045");
    expect(screen.getByText(/asked \$20\.00 → armed \$10\.00: allowance/)).toBeTruthy();
  });
});
