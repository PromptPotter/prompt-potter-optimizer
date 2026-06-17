"use client";
import {
  isOptimizerNodeId,
  OptimizerNodeDetail,
  WorkflowCanvas,
  type PipelineDoc,
} from "@/components/workflow";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { FamilyTree } from "@/components/dashboard/lineage/FamilyTree";
import { ScoringInspector } from "@/components/dashboard/scoring/ScoringInspector";
import { useSelection } from "@/lib/SelectionContext";

// The Now lane's main row + drill-down. Renders the Fitness + Lineage row,
// then (when a candidate is selected) the Scoring inspector on its own
// full-width row, then the optimizer pipeline canvas. The per-round samples
// view is no longer a standalone card — it lives inside the l1_score node
// panel (OptimizerNodeDetail), surfaced only when that node is clicked.
//
// Every region reads its own state from context (`useDashboard`,
// `useWorkspace`, `useSelection`); the only thing threaded is `pipeline` (a
// one-shot topology read with no context home). A click in any one surface
// re-anchors the others through `useSelection` — fitness ↔ lineage ↔ inspector
// ↔ samples stay structurally locked together. The lineage fetch + its
// mask/lens divergence overlay are owned by `LineageOverlayProvider` at the
// shell root.

interface Props {
  pipeline: PipelineDoc | null;
}

export function NowTriad({ pipeline }: Props) {
  const { candidate, node, setSelectionForCandidate, setSelectionForNode } =
    useSelection();
  return (
    <>
      <div className="dash-row-triad">
        <FitnessPanel />
        <FamilyTree />
      </div>
      {candidate && (
        <div className="card inspector-row">
          <ScoringInspector
            selected={candidate}
            onClose={() => setSelectionForCandidate(null)}
          />
        </div>
      )}
      <WorkflowCanvas pipeline={pipeline} />
      {node && isOptimizerNodeId(node) && (
        <OptimizerNodeDetail
          id={node}
          pipeline={pipeline}
          onClose={() => setSelectionForNode(null)}
        />
      )}
    </>
  );
}
