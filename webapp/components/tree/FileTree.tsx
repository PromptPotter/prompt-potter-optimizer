"use client";
import { useMemo, useState } from "react";
import { fetchFiles, type FileEntry } from "@/lib/api";
import { useWorkspace } from "@/lib/workspace";
import { useFetch } from "@/lib/hooks/useFetch";
import { Empty, Loading, ErrorNote } from "@/components/ui/states";

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
    let cur = tree[scope];
    for (let i = 0; i < parts.length - 1; i += 1) {
      cur.dirs[parts[i]] = cur.dirs[parts[i]] ?? makeNode();
      cur = cur.dirs[parts[i]];
    }
    cur.files.push({
      name: parts[parts.length - 1],
      path: e.path,
      scope,
      size: e.size,
      mtime: e.mtime,
    });
  }
  return tree;
}

interface Props {
  campaignId: string | null;
  cycleId: string | null;
  selected: { scope: string; path: string } | null;
  onSelect: (scope: string, path: string) => void;
}

export function FileTree({ campaignId, cycleId, selected, onSelect }: Props) {
  // Workspace-level reachability — so the no-unit state can tell a network
  // failure apart from a genuinely empty workspace.
  const { activeError, cyclesError } = useWorkspace();
  const { data: listing, error } = useFetch(
    campaignId && cycleId ? (s) => fetchFiles(campaignId, cycleId, s) : null,
    [campaignId, cycleId],
  );
  const tree = useMemo(
    () => (listing ? buildTree(listing.entries) : null),
    [listing],
  );

  if (!cycleId) {
    return activeError || cyclesError ? (
      <ErrorNote>Server unreachable — retrying</ErrorNote>
    ) : (
      <Empty>No active campaign — pick one from the sidebar, or start one in a terminal.</Empty>
    );
  }
  if (error) {
    return <ErrorNote>Failed to load files: {error}</ErrorNote>;
  }
  if (!tree) {
    return <Loading>Loading file tree…</Loading>;
  }

  return (
    <div>
      <ScopeBlock label="unit (root)" node={tree.cycle} scope="cycle" selected={selected} onSelect={onSelect} />
      <ScopeBlock label="campaign (telemetry)" node={tree.campaign} scope="campaign" selected={selected} onSelect={onSelect} />
    </div>
  );
}

interface ScopeBlockProps {
  label: string;
  node: DirNode;
  scope: string;
  selected: { scope: string; path: string } | null;
  onSelect: (scope: string, path: string) => void;
}

function ScopeBlock({ label, node, scope, selected, onSelect }: ScopeBlockProps) {
  if (Object.keys(node.dirs).length === 0 && node.files.length === 0) return null;
  return (
    <DirBlock name={label} node={node} scope={scope} selected={selected} onSelect={onSelect} topLevel />
  );
}

interface DirBlockProps {
  name: string;
  node: DirNode;
  scope: string;
  selected: { scope: string; path: string } | null;
  onSelect: (scope: string, path: string) => void;
  topLevel?: boolean;
}

function DirBlock({ name, node, scope, selected, onSelect, topLevel }: DirBlockProps) {
  const [collapsed, setCollapsed] = useState(false);
  const dirs = Object.keys(node.dirs).sort();
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
        {dirs.map((d) => (
          <DirBlock key={d} name={d} node={node.dirs[d]} scope={scope} selected={selected} onSelect={onSelect} />
        ))}
        {files.map((f) => {
          const isSelected = selected?.scope === f.scope && selected?.path === f.path;
          return (
            <button
              key={f.path}
              type="button"
              className={`tree-row${isSelected ? " selected" : ""}`}
              title={f.path}
              aria-label={`Open file ${f.name}`}
              aria-current={isSelected ? "true" : undefined}
              onClick={() => onSelect(f.scope, f.path)}
            >
              {f.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}
