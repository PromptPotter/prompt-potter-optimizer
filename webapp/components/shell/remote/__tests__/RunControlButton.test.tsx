import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeStream } from "@/lib/__tests__/fake-stream";
import { DEFAULT_VIEWS, NO_KEYS, formatAddress } from "@/lib/address";
import { commands } from "@/lib/api";
import { ApiError } from "@/lib/api/client";
import { clearIncidents } from "@/lib/diagnostics";
import { clearReadCache } from "@/lib/read-cache";
import { dash } from "@/lib/test-fixtures";
import { InWorkspace } from "@/lib/test-utils/in-workspace";
import { RunControlButton } from "../RunControlButton";

// The viewed cycle, the link the test moves; both cycles run, so each offers a pause.
const view = (cycleId: string) =>
  act(() => {
    const path = [{ campaignId: "c", cycleId }];
    window.history.replaceState(null, "", formatAddress({ subject: { path }, views: DEFAULT_VIEWS, keys: NO_KEYS }));
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

const PAUSING = /will pause after the current sample/;

describe("RunControlButton — a pause answers on the cycle it was sent from", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeStream);
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      return path.endsWith("/dashboard") ? Response.json(dash()) : new Response(null, { status: 404 });
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
      view("A");
      render(
        <InWorkspace>
          <RunControlButton />
        </InWorkspace>,
      );
      fireEvent.click(await screen.findByRole("button", { name: "Pause run" }));
      expect(screen.getByText(PAUSING)).toBeTruthy();
      view("B");
      await act(async () => answer(settle));
      expect(screen.queryByText(PAUSING)).toBeNull();
      expect(screen.queryByRole("alert")).toBeNull();
      expect((await screen.findByRole("button", { name: "Pause run" })).hasAttribute("disabled")).toBe(false);
    });
  }
});
