import { useState } from "react";
import { useWorkspace } from "@/lib/state/workspace";
import { FileSelection, FileTree, type FilePick } from "./FileTree";
import { FileViewer } from "./FileViewer";

export function FilesPane() {
  const { viewed } = useWorkspace();
  const campaignId = viewed?.root.campaignId ?? null;
  const cycleId = viewed?.root.cycleId ?? null;
  const [selected, setSelected] = useState<FilePick | null>(null);
  return (
    <div className="files-pane box-full">
      <div className="tree-pane" role="navigation" aria-label="Files">
        <FileSelection.Provider value={[selected, setSelected]}>
          <FileTree campaignId={campaignId} cycleId={cycleId} />
        </FileSelection.Provider>
      </div>
      <FileViewer campaignId={campaignId} cycleId={cycleId} selected={selected} />
    </div>
  );
}
