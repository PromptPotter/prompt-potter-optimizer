"use client";
import { Button } from "@/components/ui";
import { WORKSPACE_LABEL, tabLabel, type WorkspaceTab } from "@/lib/view-tab";

// The header of a WORKSPACE view — one that reads across campaigns. It stands where the run
// masthead stands on a campaign view, and says nothing about any one run.
export function WorkspaceHeader({
  tab,
  onBack,
  backLabel,
}: {
  tab: WorkspaceTab;
  // Absent when no campaign is in view to go back to.
  onBack?: () => void;
  backLabel: string;
}) {
  return (
    <header className="workspace-header">
      <span className="workspace-title">
        {WORKSPACE_LABEL} · {tabLabel(tab)}
      </span>
      {onBack && (
        <Button className="workspace-back" onClick={onBack}>
          ← {backLabel}
        </Button>
      )}
    </header>
  );
}
