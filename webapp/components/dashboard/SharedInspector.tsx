"use client";
import { ScoringInspector } from "./ScoringInspector";
import { NodePanel, type PipelineDoc, type NodeDataLike } from "@/components/workflow/WorkflowCanvas";
import { useSelection } from "./SelectionContext";
import { roundOf, type DashboardSnapshot } from "@/lib/poll";

interface Props {
  cycleId: string | null;
  refreshKey: number;
  dash: DashboardSnapshot | null;
  pipeline: PipelineDoc | null;
  isLive: boolean;
}

// Single inspector slot fed by all three top-row panels (lineage,
// fitness, workflow). Lineage + fitness write to `selected`; workflow
// writes to `node`. Each surface renders independently and they can
// both be on at once — picking a candidate AND a node narrows from
// "this searchpoint" to "this node within this searchpoint".
export function SharedInspector({ cycleId, refreshKey, dash, pipeline, isLive }: Props) {
  const { selected, setSelected, node, setNode } = useSelection();

  if (!selected && !node) {
    return (
      <div className="shared-inspector empty">
        Select a lineage stub, a fitness bar, or a workflow node above to
        inspect its searchpoint and details here.
      </div>
    );
  }

  const view = pipeline?.view;
  const currentNodes = (dash?.current_round?.nodes ?? {}) as Record<string, NodeDataLike>;
  const phase = dash?.state;
  const round = roundOf(dash) ?? undefined;

  return (
    <div className="shared-inspector">
      {selected && (
        <ScoringInspector
          cycleId={cycleId}
          refreshKey={refreshKey}
          selected={selected}
          isLive={isLive}
          onClose={() => setSelected(null)}
        />
      )}
      {node && view && (
        <NodePanel
          id={node}
          view={view}
          currentNodes={currentNodes}
          pipeline={pipeline}
          phase={phase}
          round={round}
          onClose={() => setNode(null)}
        />
      )}
    </div>
  );
}
