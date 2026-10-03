"use client";
import { useState } from "react";
import { FileTree } from "./FileTree";
import { FileViewer } from "./FileViewer";
import { StorageCakes } from "./StorageCakes";
import { useCycleStream, type DashboardSnapshot } from "@/lib/poll";

interface Props {
  campaignId: string | null;
  cycleId: string | null;
}

function RawJsonCard({ dash }: { dash: DashboardSnapshot | null }) {
  return (
    <div className="card raw-card">
      <details>
        <summary>Raw dashboard.json</summary>
        <pre>
          {dash ? JSON.stringify(dash, null, 2) : "Waiting for first poll…"}
        </pre>
      </details>
    </div>
  );
}

export function FilesPane({ campaignId, cycleId }: Props) {
  const [selected, setSelected] = useState<{ scope: string; path: string } | null>(null);
  const { dash } = useCycleStream();
  return (
    <div className="content files-content" id="content-files">
      <StorageCakes />
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
