"use client";
import { useState } from "react";
import { FileTree } from "./FileTree";
import { FileViewer } from "./FileViewer";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
}

export function FilesPane({ campaignId, cycleId }: Props) {
  const [selected, setSelected] = useState<{ scope: string; path: string } | null>(null);
  return (
    <div className="content" id="content-files" style={{ height: "100%" }}>
      <div className="files-pane">
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
    </div>
  );
}
