"use client";
import { Button } from "@/components/ui";
import { WORKSPACE_LABEL, tabLabel, type WorkspaceTab } from "@/lib/view-tab";
import { useWorkspace } from "@/lib/workspace";

export function WorkspaceHeader({ tab }: { tab: WorkspaceTab }) {
  const { campaignId, backToCampaign } = useWorkspace();
  return (
    <header className="workspace-header">
      <span className="workspace-title">
        {WORKSPACE_LABEL} · {tabLabel(tab)}
      </span>
      {campaignId && (
        <Button className="workspace-back" onClick={backToCampaign}>
          ← Back to campaign
        </Button>
      )}
    </header>
  );
}
