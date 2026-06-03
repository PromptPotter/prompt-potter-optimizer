"use client";
import type { DashboardSnapshot, StatusKind } from "@/lib/poll";
import { WorkflowCanvas } from "@/components/workflow/WorkflowCanvas";
import { OptimizerNodeDetail } from "@/components/workflow/OptimizerNodeDetail";
import { isOptimizerNodeId } from "@/components/workflow/layout";
import { FitnessPanel } from "@/components/whatif/FitnessPanel";
import { LineageTree } from "@/components/dashboard/lineage/LineageTree";
import { RoundSamplesView } from "@/components/dashboard/samples/RoundSamplesView";
import { ScoringInspector } from "@/components/dashboard/scoring/ScoringInspector";
import { useSelection } from "@/lib/SelectionContext";
import type { PipelineDoc } from "@/components/workflow/types";

// The Now lane's main row + drill-down. Renders the triad (Fitness +
// Lineage + Inspector) as one grid, then the per-round samples view,
// then the optimizer pipeline canvas. When a candidate is selected
// the Inspector slides in as the triad's third column — when nothing
// is selected the row stays a tight 2-col with no empty placeholder.
//
// All three triad cards share `useSelection`: a click in any one
// re-anchors the others through (candidate, round) — fitness ↔
// lineage ↔ inspector ↔ samples are structurally locked together.
// Extracted out of AppShell to keep the page-level component a
// thin shell + lanes; the triad's own coupling lives here.

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
      <div className="dash-row-triad">
        <FitnessPanel dash={dash} dashRound={dashRound} cycleId={cycleId} />
        <LineageTree
          dash={dash}
          campaignId={campaignId}
          cycleId={cycleId}
          onSelectCycle={onSelectCycle}
        />
        {candidate && (
          <div className="card inspector-panel">
            <ScoringInspector
              campaignId={campaignId}
              cycleId={cycleId}
              selected={candidate}
              dash={dash}
              onClose={() => setSelectionForCandidate(null)}
            />
          </div>
        )}
      </div>
      <RoundSamplesView
        dash={dash}
        status={status}
        campaignId={campaignId}
        cycleId={cycleId}
      />
      <WorkflowCanvas pipeline={pipeline} dash={dash} isLive={isLive} />
      {node && isOptimizerNodeId(node) && (
        <OptimizerNodeDetail
          id={node}
          pipeline={pipeline}
          dash={dash}
          isLive={isLive}
          campaignId={campaignId}
          cycleId={cycleId}
          onClose={() => setSelectionForNode(null)}
        />
      )}
    </>
  );
}
