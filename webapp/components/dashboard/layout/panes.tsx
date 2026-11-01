"use client";
// The dashboard's view families: each a run of `.box-grid` boxes, read top-down in the preset's order.
import { CardFrame } from "@/components/ui";
import { useConnector } from "@/lib/hooks/useConnector";
import { useCandidatesState } from "@/lib/state/candidates";
import { useSelection } from "@/lib/SelectionContext";
import { hasTag } from "@/lib/derivations";
import { useEntry } from "@/lib/state/registry";
import { useWorkspace } from "@/lib/workspace";
import { TopStrip } from "./TopStrip";
import { TimeRay } from "./TimeRay";
import { CandidatesCard } from "@/components/candidates/CandidatesCard";
import { ForestCard } from "@/components/candidates/ForestCard";
import { BackendRetries } from "@/components/dashboard/pipeline/BackendRetries";
import { BackendRevisionDrift } from "@/components/dashboard/pipeline/BackendRevisionDrift";
import { OptimizerCard } from "@/components/dashboard/pipeline/OptimizerCard";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { FreqChart } from "@/components/dashboard/scoring/FreqChart";
import { OuterSignalPanel } from "@/components/dashboard/scoring/OuterSignalPanel";
import { ScoringInspector } from "@/components/dashboard/scoring/ScoringInspector";
import { SubjectBox } from "@/components/shell/searchpoint/SubjectBox";

export function TimelinePane() {
  return (
    <>
      <TopStrip />
      <div className="box-full">
        <TimeRay />
      </div>
    </>
  );
}

export function LeaderPane() {
  const { viewedPath, campaignId } = useWorkspace();
  const rootSelfOpt = hasTag(useEntry(campaignId), "self_optimization");
  return (
    <>
      <CardFrame title="Leader" headingTag="h2">
        <SubjectBox />
      </CardFrame>
      {viewedPath?.length === 1 && rootSelfOpt && <OuterSignalPanel />}
    </>
  );
}

export function CandidatesPane() {
  const { candidate, setSelectionForCandidate } = useSelection();
  const { showForest } = useCandidatesState();
  return (
    <>
      <CandidatesCard />
      {showForest && <ForestCard />}
      {candidate && (
        <div className="card card-full inspector-card">
          <ScoringInspector selected={candidate} onClose={() => setSelectionForCandidate(null)} />
        </div>
      )}
    </>
  );
}

// An L4 unit has no sample roster — its samples ARE the inner campaigns.
export function HeatmapPane() {
  if (useConnector().selfOptimization) return null;
  return (
    <CardFrame title="Heatmap" headingTag="h2" span="full">
      <HardSamplesHeatmap />
      <div className="card-inset">
        <FreqChart />
      </div>
    </CardFrame>
  );
}

export function OptimizerPane() {
  return (
    <>
      <BackendRevisionDrift />
      <BackendRetries />
      <OptimizerCard />
    </>
  );
}
