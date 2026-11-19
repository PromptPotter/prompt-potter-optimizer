import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { commands } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { clearIncidents } from "@/lib/diagnostics";
import { AuthProvider } from "@/lib/state/auth";
import { dash } from "@/lib/test-fixtures";
import { RunControlButton } from "../RunControlButton";

// The viewed cycle, moved by the test; both cycles run, so each offers a pause.
const at = (cycleId: string) => ({ path: [{ campaignId: "c", cycleId }], address: `c/${cycleId}` });
let viewed = at("A");
vi.mock("@/lib/state/workspace", () => ({ useWorkspace: () => ({ viewed }) }));
vi.mock("@/lib/state/poll", () => ({ useViewedDashboard: () => ({ dash: dash() }) }));

const PAUSING = /will pause after the current sample/;

describe("RunControlButton — a pause answers on the cycle it was sent from", () => {
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

  const answers: [string, (settle: { resolve: (v: never) => void; reject: (e: unknown) => void }) => void][] = [
    ["landed", (s) => s.resolve({} as never)],
    ["refused", (s) => s.reject(new ApiError(409, "/commands/pause"))],
  ];
  for (const [answered, answer] of answers) {
    it(`paints nothing on B when A's pause settles ${answered} after the view moved`, async () => {
      let settle!: { resolve: (v: never) => void; reject: (e: unknown) => void };
      vi.spyOn(commands, "pause").mockReturnValue(
        new Promise<never>((resolve, reject) => (settle = { resolve, reject })),
      );
      const ui = () => (
        <AuthProvider>
          <RunControlButton />
        </AuthProvider>
      );
      const { rerender } = render(ui());
      fireEvent.click(screen.getByRole("button", { name: "Pause run" }));
      expect(screen.getByText(PAUSING)).toBeTruthy();
      viewed = at("B");
      rerender(ui());
      await act(async () => answer(settle));
      expect(screen.queryByText(PAUSING)).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
      expect(screen.getByRole("button", { name: "Pause run" }).hasAttribute("disabled")).toBe(false);
    });
  }
});
