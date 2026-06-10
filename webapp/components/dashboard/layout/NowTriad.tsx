"use client";
import type { DashboardSnapshot, StatusKind } from "@/lib/poll";
import { WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
import { OptimizerNodeDetail } from "@/components/workflow/OptimizerNodeDetail";
import { isOptimizerNodeId } from "@/components/workflow/layout";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { FamilyTree } from "@/components/dashboard/lineage/FamilyTree";
import { ScoringInspector } from "@/components/dashboard/scoring/ScoringInspector";
import { useSelection } from "@/lib/SelectionContext";
import { LineageOverlayProvider } from "@/lib/lineage-overlay";
import type { PipelineDoc } from "@/components/workflow/types";

// The Now lane's main row + drill-down. Renders the Fitness + Lineage row,
// then (when a candidate is selected) the Scoring inspector on its own
// full-width row, then the optimizer pipeline canvas. The per-round samples
// view is no longer a standalone card — it lives inside the l1_score node
// panel (OptimizerNodeDetail), surfaced only when that node is clicked.
//
// All surfaces share `useSelection`: a click in any one re-anchors the
// others through (candidate, round) — fitness ↔ lineage ↔ inspector ↔
// samples stay structurally locked together. Extracted out of AppShell to
// keep the page-level component a thin shell + lanes.

interface Props {
  dash: DashboardSnapshot | null;
  dashRound: number | null;
  status: StatusKind;
  pipeline: PipelineDoc | null;
  campaignId: string | null;
  cycleId: string | null;
  onSelectCycle: (campaignId: string, cycleId: string) => void;
  isLive: boolean;
}

export function NowTriad({
  dash,
  dashRound,
  status,
  pipeline,
  campaignId,
  cycleId,
  onSelectCycle,
  isLive,
}: Props) {
  const { candidate, node, setSelectionForCandidate, setSelectionForNode } =
    useSelection();
  return (
    <>
      {/* The lineage fetch + its mask/lens divergence overlay are owned here, once,
          and read by BOTH the fitness panel and the lineage card — one served
          overlay, no cross-widget module global (R-36). */}
      <LineageOverlayProvider campaignId={campaignId}>
        <div className="dash-row-triad">
          <FitnessPanel dash={dash} dashRound={dashRound} cycleId={cycleId} />
          <FamilyTree
            dash={dash}
            campaignId={campaignId}
            cycleId={cycleId}
            onSelectCycle={onSelectCycle}
          />
        </div>
      </LineageOverlayProvider>
      {candidate && (
        <div className="card inspector-row">
          <ScoringInspector
            campaignId={campaignId}
            cycleId={cycleId}
            selected={candidate}
            dash={dash}
            onClose={() => setSelectionForCandidate(null)}
          />
        </div>
      )}
      <WorkflowCanvas pipeline={pipeline} dash={dash} isLive={isLive} />
      {node && isOptimizerNodeId(node) && (
        <OptimizerNodeDetail
          id={node}
          pipeline={pipeline}
          dash={dash}
          status={status}
          isLive={isLive}
          campaignId={campaignId}
          cycleId={cycleId}
          onClose={() => setSelectionForNode(null)}
        />
      )}
    </>
  );
}
