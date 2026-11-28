import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Filable, ListedCampaign } from "@/lib/api/types";
import { useFiles } from "@/lib/hooks/useFiles";
import { clearReadCache } from "@/lib/read-cache";
import { ThreadProvider } from "@/lib/state/chat-thread";
import { IngestFlowProvider } from "@/lib/state/ingest-flow";
import { LISTED_DELETE, OWNER_FILABLE, subtreeCampaign } from "@/lib/test-fixtures";
import { InWorkspace } from "@/lib/test-utils/in-workspace";
import { CampaignMenu } from "../sidebar/CampaignMenu";

// A `/grant` delegate reads the delegator's account with narrowed kinds (`/auth/me`'s `filable`).
const LIFECYCLE: Filable[] = ["rename-campaign", "archive-campaign", "unarchive-campaign", "delete-campaign"];

let probes = 0;

function serve(filable: Filable[]): void {
  probes = 0;
  vi.stubGlobal("fetch", async (url: string) => {
    if (new URL(url, "http://x").pathname.endsWith("/auth/me")) {
      probes += 1;
      return Response.json({ tenant_id: "t", user_id: "u", filable });
    }
    return new Response(null, { status: 404 });
  });
}

// Drawn once the account's kinds are read: a kind every reader here files.
function Read() {
  return useFiles("account")("mint-campaign") ? <p>read</p> : null;
}

// The session's one probe answers the kinds too: they stand once it lands.
async function view() {
  // The listing's entry: the subtree's, less the facts only the deep read serves.
  const { facts: f, ...entry } = subtreeCampaign({ address: "camp-1" });
  const { label, short_label, root, root_label, live_cycle, lifecycle, runs_with } = f;
  const facts = { label, short_label, root, root_label, live_cycle, lifecycle, runs_with };
  const listed: ListedCampaign = { ...entry, delete: LISTED_DELETE, children: [], facts };
  render(
    <InWorkspace>
      <ThreadProvider>
        <IngestFlowProvider>
          <Read />
          <CampaignMenu campaign={listed} />
        </IngestFlowProvider>
      </ThreadProvider>
    </InWorkspace>,
  );
  await waitFor(() => expect(probes).toBe(1));
  expect(await screen.findByText("read")).toBeTruthy();
  expect(probes).toBe(1);
}

describe("a delegate is offered only the campaign acts its grant files", () => {
  beforeEach(() => clearReadCache(true));
  afterEach(() => {
    cleanup();
    clearReadCache(false);
    vi.unstubAllGlobals();
  });

  it("the owner renames, archives and deletes", async () => {
    serve(OWNER_FILABLE);
    await view();
    fireEvent.click(screen.getByRole("button", { name: "Campaign actions" }));
    for (const item of ["Rename", "Archive", "Delete"]) {
      expect(await screen.findByRole("menuitem", { name: item })).toBeTruthy();
    }
  });

  it("a delegate without `lifecycle` is offered none of them", async () => {
    serve(OWNER_FILABLE.filter((kind) => !LIFECYCLE.includes(kind)));
    await view();
    expect(screen.queryByRole("button", { name: "Campaign actions" })).toBeNull();
  });
});
