"use client";
import { WorkflowCanvas, type PipelineDoc } from "@/components/workflow";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { CandidatesCard } from "@/components/candidates/CandidatesCard";
import { ForestCard } from "@/components/candidates/ForestCard";
import { useCandidatesState } from "@/components/candidates/candidates-store";
import { ScoringInspector } from "@/components/dashboard/scoring/ScoringInspector";
import { useSelection } from "@/lib/SelectionContext";

// The Now lane: the Optimizer card (round axis + pipeline canvas) and the
// Candidates card (bars + genealogy) share one row, optimizer first — you read
// what the loop is DOING, then what it PRODUCED. Both size to their content, so
// the row wraps on its own: the moment Candidates needs the full band (Forest
// view, or What-If opening its second column), it drops to the next line instead
// of squeezing the optimizer. No breakpoint decides that; the content does.
//
// The drill-downs then stack full-width below: the Scoring inspector when a
// candidate is selected, the node panel when a node is — EITHER canvas's, since
// `NodeDetail` is one panel for both scopes and the tab a node was clicked on no
// longer decides whether it opens. The per-round samples view is not a standalone
// card either — it lives inside the l1_score node panel.
//
// Every region reads its own state from context (`useDashboard`,
// `useWorkspace`, `useSelection`); the only thing threaded is `pipeline` (a
// one-shot topology read with no context home). A click in any one surface
// re-anchors the others through `useSelection`: picking a candidate moves the
// round axis, so the Optimizer card follows to the round that PRODUCED it. The
// lineage fetch + its mask/lens divergence overlay are owned by
// `LineageProvider` at the shell root.

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
        <WorkflowCanvas pipeline={pipeline} />
        <CandidatesCard />
      </div>
      {/* The lineage forest is its OWN card, opened by the toggle beside the
          dendrogram. It shares no axis with the bars — it is a cladogram of
          CYCLES on its own round-column grid — so it gets its own box and its
          own width rather than being bound to the chart's geometry. */}
      {showForest && <ForestCard />}
      {candidate && (
        <div className="card inspector-row">
          <ScoringInspector
            selected={candidate}
            onClose={() => setSelectionForCandidate(null)}
          />
        </div>
      )}
      {/* Either scope: the panel is the node's, not the tab's. A target node picked
          on the chat hero stays open when the operator crosses to Dashboard. */}
      {node && (
        <NodeDetail
          node={node}
          draft={null}
          onClose={() => setSelectionForNode(null)}
        />
      )}
    </>
  );
}
