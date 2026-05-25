"use client";
import { useState } from "react";
import { FileTree } from "./FileTree";
import { FileViewer } from "./FileViewer";
import { RawJsonCard } from "@/components/raw/RawJsonCard";
import { useCycleStream } from "@/lib/poll";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
}

export function FilesPane({ campaignId, cycleId }: Props) {
  const [selected, setSelected] = useState<{ scope: string; path: string } | null>(null);
  // RawJsonCard sits with the on-disk artifacts it dumps — the operator
  // who wants the raw dashboard.json blob is the same operator browsing
  // the rest of the file tree, not the one watching the live drill.
  const { dash } = useCycleStream();
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
      <div className="files-pane-footer">
        <RawJsonCard dash={dash} />
      </div>
    </div>
  );
}
