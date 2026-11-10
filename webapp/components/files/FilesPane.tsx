import { useState } from "react";
import { useWorkspace } from "@/lib/workspace";
import { FileTree } from "./FileTree";
import { FileViewer } from "./FileViewer";

export function FilesPane() {
  const { viewed } = useWorkspace();
  const campaignId = viewed?.root.campaignId ?? null;
  const cycleId = viewed?.root.cycleId ?? null;
  const [selected, setSelected] = useState<{ scope: string; path: string } | null>(null);
  return (
    <div className="files-pane box-full">
      <div className="tree-pane" role="navigation" aria-label="Files">
        <FileTree
          campaignId={campaignId}
          cycleId={cycleId}
          selected={selected}
          onSelect={(scope, path) => setSelected({ scope, path })}
        />
      </div>
      <FileViewer campaignId={campaignId} cycleId={cycleId} selected={selected} />
    </div>
  );
}
