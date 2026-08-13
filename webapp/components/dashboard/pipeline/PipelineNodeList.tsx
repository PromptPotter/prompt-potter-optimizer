"use client";
import type { NodeConfigParam } from "@/lib/api";
import type { PipelineViewNode } from "@/components/workflow";
import type { NodeScope } from "@/lib/SelectionContext";
import { useSelection } from "@/lib/SelectionContext";

interface Props {
  // Interior nodes in pipeline order — the CALLER filters and decides whether this layer
  // is worth drawing, since only it knows what a missing layer means.
  nodes: PipelineViewNode[];
  schema: Record<string, NodeConfigParam[]> | null;
  // Which namespace a click writes. Node ids are NOT disjoint across pipelines, so the
  // writer names its canvas — see `SelectionContext::NodeScope`.
  scope: NodeScope;
}

// Plain inline list of one pipeline layer's nodes. Each is a button selecting it into the
// same SelectionContext.node axis the hero writes; it maps straight over the served view,
// so a new node needs no code here. Near-unstyled on purpose: mechanics, not polish.
export function PipelineNodeList({ nodes, schema, scope }: Props) {
  const { node: selected, setSelectionForNode } = useSelection();
  if (nodes.length === 0) return null;
  return (
    <ol className="pipeline-node-list" aria-label="Pipeline nodes">
      {nodes.map((n, i) => {
        // Locked = a served param list with nothing tunable in it; a paramless node is
        // locked too. A null schema is UNKNOWN and gets no badge, never a false "open".
        // Correct only while the served list stays COMPLETE — a narrowed one (widgets
        // only, no prose) reads a prose-only node as locked.
        const params = schema?.[n.id];
        const locked = params != null && params.every((p) => !p.optimizer_tunable);
        const isSelected = selected?.scope === scope && selected.id === n.id;
        return (
          <li key={n.id}>
            {i > 0 ? <span aria-hidden="true">→</span> : null}
            <button
              type="button"
              aria-pressed={isSelected}
              onClick={() => setSelectionForNode(isSelected ? null : { id: n.id, scope })}
            >
              {locked ? (
                <span className="pnl-lock" title="Optimizer-locked" aria-label="optimizer-locked">
                  🔒
                </span>
              ) : null}
              {n.label}
              <small> ({n.kind ?? "node"})</small>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
