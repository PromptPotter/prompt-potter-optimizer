import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_VIEWS, NO_KEYS, formatAddress } from "@/lib/address";
import { clearReadCache } from "@/lib/read-cache";
import { InWorkspace } from "@/lib/test-utils/in-workspace";
import { SubjectBox } from "../SubjectBox";

describe("SubjectBox — the Leader card says why it shows no leader (I1)", () => {
  beforeEach(() => {
    clearReadCache(true);
    window.history.replaceState(
      null,
      "",
      formatAddress({ subject: { path: [{ campaignId: "c", cycleId: "y" }] }, views: DEFAULT_VIEWS, keys: NO_KEYS }),
    );
    // Signed in; the viewed cycle's dashboard is refused, every other read is empty.
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/dashboard")) return Response.json({ detail: "no", error_id: "e" }, { status: 403 });
      return new Response(null, { status: 404 });
    });
  });
  afterEach(() => {
    window.history.replaceState(null, "", window.location.pathname);
    vi.unstubAllGlobals();
  });

  it("names its failure where the viewed run's dashboard is not read", async () => {
    render(
      <InWorkspace>
        <SubjectBox />
      </InWorkspace>,
    );
    expect(await screen.findByText(/Could not load the viewed run's leader/)).toBeTruthy();
  });
});
