import { createContext, useContext, useMemo, useState } from "react";
import { filesRead } from "@/lib/api";
import type { FileEntry } from "@/lib/api/types";
import { useActivePointer, useRegistry } from "@/lib/hooks/useRegistry";
import { failureOf, useRead } from "@/lib/hooks/useRead";
import { Empty, FailureLine, ReadState } from "@/components/ui";

interface DirNode {
  dirs: Record<string, DirNode>;
  files: FileLeaf[];
}

interface FileLeaf {
  name: string;
  path: string;
  scope: string;
  size?: number;
  mtime?: string;
}

interface ScopedTree {
  cycle: DirNode;
  campaign: DirNode;
}

function makeNode(): DirNode {
  return { dirs: {}, files: [] };
}

function buildTree(entries: FileEntry[]): ScopedTree {
  const tree: ScopedTree = { cycle: makeNode(), campaign: makeNode() };
  for (const e of entries) {
    const scope = e.scope === "campaign" ? "campaign" : "cycle";
    const parts = e.path.split("/");
    const name = parts.pop();
    if (!name) continue;
    let cur = tree[scope];
    for (const seg of parts) {
      const child = cur.dirs[seg] ?? makeNode();
      cur.dirs[seg] = child;
      cur = child;
    }
    cur.files.push({
      name,
      path: e.path,
      scope,
      size: e.size,
      mtime: e.mtime,
    });
  }
  return tree;
}

export type FilePick = { scope: string; path: string };

// The Files pane's one picked file and its one writer, read by the row that picks it.
export const FileSelection = createContext<[FilePick | null, (f: FilePick) => void]>([null, () => {}]);

export function FileTree({ campaignId, cycleId }: { campaignId: string | null; cycleId: string | null }) {
  const registryFailure = failureOf(useRegistry().read);
  const pointerFailure = failureOf(useActivePointer().read);
  const read = useRead(campaignId && cycleId ? filesRead(campaignId, cycleId) : null);
  const netFailure = pointerFailure ?? registryFailure;

  return (
    <ReadState
      read={read}
      subject="this cycle’s files"
      idle={
        netFailure !== null ? (
          <FailureLine failure={netFailure} />
        ) : (
          <Empty>No active campaign — pick one from the sidebar, or start one in a terminal.</Empty>
        )
      }
    >
      {(listing) => <FileScopes entries={listing.entries} />}
    </ReadState>
  );
}

function FileScopes({ entries }: { entries: FileEntry[] }) {
  const tree = useMemo(() => buildTree(entries), [entries]);
  return (
    <div>
      <ScopeBlock label="unit (root)" node={tree.cycle} scope="cycle" />
      <ScopeBlock label="campaign (telemetry)" node={tree.campaign} scope="campaign" />
    </div>
  );
}

interface DirBlockProps {
  name: string;
  node: DirNode;
  scope: string;
  topLevel?: boolean;
}

function ScopeBlock({ label, node, scope }: Omit<DirBlockProps, "name" | "topLevel"> & { label: string }) {
  if (Object.keys(node.dirs).length === 0 && node.files.length === 0) return null;
  return <DirBlock name={label} node={node} scope={scope} topLevel />;
}

function DirBlock({ name, node, scope, topLevel }: DirBlockProps) {
  const [selected, onSelect] = useContext(FileSelection);
  const [collapsed, setCollapsed] = useState(false);
  const dirs = Object.entries(node.dirs).sort(([a], [b]) => a.localeCompare(b));
  const files = node.files.slice().sort((a, b) => a.name.localeCompare(b.name));
  return (
    <div className={`tree-node${collapsed ? " tree-collapsed" : ""}`}>
      <button
        type="button"
        className="tree-dir"
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((c) => !c)}
      >
        <span className="tree-arrow" aria-hidden="true">{collapsed ? "▸" : "▾"}</span>
        {topLevel ? name : `${name}/`}
      </button>
      <div className="tree-children">
        {dirs.map(([d, child]) => (
          <DirBlock key={d} name={d} node={child} scope={scope} />
        ))}
        {files.map((f) => {
          const isSelected = selected?.scope === f.scope && selected.path === f.path;
          return (
            <button
              key={f.path}
              type="button"
              className={`tree-row${isSelected ? " selected" : ""}`}
              title={f.path}
              aria-label={`Open file ${f.name}`}
              aria-current={isSelected ? "true" : undefined}
              onClick={() => onSelect({ scope: f.scope, path: f.path })}
            >
              {f.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}
