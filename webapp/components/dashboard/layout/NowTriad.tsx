"use client";
import { OptimizerCard, type PipelineDoc } from "@/components/workflow";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { CandidatesCard } from "@/components/candidates/CandidatesCard";
import { ForestCard } from "@/components/candidates/ForestCard";
import { useCandidatesState } from "@/components/candidates/candidates-store";
import { ScoringInspector } from "@/components/dashboard/scoring/ScoringInspector";
import { useSelection } from "@/lib/SelectionContext";

// The Now lane: Optimizer then Candidates card on one wrapping row, drill-downs full-width below.
// Regions read their own context; only `pipeline` is threaded (it has no context home).

interface Props {
  pipeline: PipelineDoc | null;
}

export function NowTriad({ pipeline }: Props) {
  const { candidate, node, setSelectionForCandidate, setSelectionForNode } =
    useSelection();
  const { showForest } = useCandidatesState();
  return (
    <>
      <div className="dash-now-row">
        <OptimizerCard pipeline={pipeline} />
        <CandidatesCard />
      </div>
      {/* Its own card: the forest shares no axis with the bars. */}
      {showForest && <ForestCard />}
      {candidate && (
        <div className="card inspector-card">
          <ScoringInspector
            selected={candidate}
            onClose={() => setSelectionForCandidate(null)}
          />
        </div>
      )}
      {/* Either scope: the panel is the node's, not the tab's. */}
      {node && (
        <NodeDetail node={node} onClose={() => setSelectionForNode(null)} />
      )}
    </>
  );
}
