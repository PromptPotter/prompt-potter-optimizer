"use client";
import { useEffect, useMemo, useState } from "react";
import { fetchFiles, type FileEntry } from "@/lib/api";

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
  const [entries, setEntries] = useState<FileEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!campaignId || !cycleId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetchFiles(campaignId, cycleId);
        if (!cancelled) setEntries(r.entries);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [campaignId, cycleId]);

  const tree = useMemo(() => (entries ? buildTree(entries) : null), [entries]);

  if (!cycleId) {
    return <div style={{ padding: 8, color: "var(--color-text-tertiary)", fontSize: 13 }}>No active campaign.</div>;
  }
  if (error) {
    return <div style={{ padding: 8, color: "var(--color-danger)", fontSize: 13 }}>Failed to load files: {error}</div>;
  }
  if (!tree) {
    return <div style={{ padding: 8, color: "var(--color-text-tertiary)", fontSize: 13 }}>Loading file tree…</div>;
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
