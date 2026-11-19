import type { ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { campaignPipelineRead } from "@/lib/api/reads";
import { clearReadCache, reaskRead } from "@/lib/read-cache";
import { configParam } from "@/lib/test-fixtures";
import { InWorkspace } from "@/lib/test-utils/in-workspace";
import { NodeConfigEditor } from "../NodeConfigEditor";

const AT = { campaignId: "c", subject: "cycle:c/y" };

const editor = (): ReactNode => (
  <InWorkspace>
    <NodeConfigEditor
      mode="search-space"
      pipelineAt={AT}
      scope="target"
      overlay={{}}
      node="n"
      readOnly={false}
      compact={undefined}
      modelCapabilities={undefined}
      onApply={() => {}}
      onNarrowing={undefined}
      onChange={undefined}
      keysAskedElsewhere={undefined}
    />
  </InWorkspace>
);

describe("NodeConfigEditor — a steer edit keys on its base's content", () => {
  // Each answer is a new body; `served` is the value it holds.
  let served: number;
  beforeEach(() => {
    served = 0.3;
    clearReadCache(true);
    vi.stubGlobal("fetch", async (url: string) => {
      const path = new URL(url, "http://x").pathname;
      if (path.endsWith("/auth/me")) return Response.json({ tenant_id: "t", user_id: "u" });
      if (path.endsWith("/pipeline")) {
        return Response.json({
          node_config_schema: { n: [configParam({ key: "temperature", value: served })] },
          is_single_node: true,
        });
      }
      return new Response(null, { status: 404 });
    });
  });
  afterEach(() => {
    clearReadCache(true);
    vi.unstubAllGlobals();
  });

  const settle = () => act(() => new Promise((r) => setTimeout(r, 10)));
  const answer = () => act(() => reaskRead(campaignPipelineRead(AT.campaignId, AT.subject).id));

  it("survives a new answer whose base holds the same values, and resets on new values", async () => {
    render(editor());
    await screen.findByText("🔒");
    await settle();
    // The served param starts held at origin; the edit opens it.
    fireEvent.click(screen.getByText("🔒"));
    expect(screen.getByText("🔓")).toBeTruthy();

    answer();
    await settle();
    expect(screen.getByText("🔓")).toBeTruthy();

    served = 0.7;
    answer();
    await waitFor(() => expect(screen.getByText("🔒")).toBeTruthy());
  });
});
