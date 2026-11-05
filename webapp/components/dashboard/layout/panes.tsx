// The dashboard's view families: each a run of `.box-grid` boxes, read top-down in the preset's order.
import { memo, useMemo } from "react";
import { CardFrame, Said } from "@/components/ui";
import { HEALTH_GRADE_INFO } from "@/lib/api/types";
import { useViewedDashboard } from "@/lib/poll";
import { useConnector } from "@/lib/hooks/useConnector";
import { shownData } from "@/lib/hooks/useRead";
import { useCandidatesState } from "@/lib/state/candidates";
import { useSelection } from "@/lib/SelectionContext";
import { hasTag } from "@/lib/derivations";
import { useEntry } from "@/lib/hooks/useRegistry";
import { useWorkspace } from "@/lib/workspace";
import { TopStrip } from "./TopStrip";
import { TimeRay } from "./TimeRay";
import { CandidatesCard } from "@/components/candidates/CandidatesCard";
import { ForestCard } from "@/components/candidates/ForestCard";
import { BackendRetries } from "@/components/dashboard/pipeline/BackendRetries";
import { BackendRevisionDrift } from "@/components/dashboard/pipeline/BackendRevisionDrift";
import { OptimizerCard } from "@/components/dashboard/pipeline/OptimizerCard";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { OuterSignalPanel } from "@/components/dashboard/scoring/OuterSignalPanel";
import { ScoringInspector } from "@/components/dashboard/scoring/ScoringInspector";
import { BenchGradeAction } from "@/components/shell/searchpoint/BenchGradeAction";
import { SubjectBox } from "@/components/shell/searchpoint/SubjectBox";

export function TimelinePane() {
  return (
    <>
      <TopStrip />
      <div className="box-full">
        <TimeRay />
      </div>
      <DegradedRounds />
    </>
  );
}

// A critical round is the run's pinned line (`dash.said`); a degraded one is read under the timeline.
const DegradedRounds = memo(function DegradedRounds() {
  const { dash } = useViewedDashboard();
  const rounds = dash?.rounds;
  const degraded = useMemo(() => (rounds ?? []).filter((r) => r.health.grade === "degraded"), [rounds]);
  if (degraded.length === 0) return null;
  return (
    <ul className="rounds-degraded box-full" aria-label="Degraded rounds">
      {degraded.map(({ round, health: { grade, reading } }) =>
        reading ? (
          <li key={round}>
            <Said tone={HEALTH_GRADE_INFO[grade].tone} label={reading.title} detail={reading.advice} />
          </li>
        ) : null,
      )}
    </ul>
  );
});

export function LeaderPane() {
  const { viewedPath, campaignId } = useWorkspace();
  const rootSelfOpt = hasTag(useEntry(campaignId), "self_optimization");
  return (
    <>
      <CardFrame title="Leader" headingTag="h2" actions={<BenchGradeAction />}>
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
  if (shownData(useConnector())?.self_optimization) return null;
  return (
    <CardFrame title="Heatmap" headingTag="h2" span="full">
      <HardSamplesHeatmap />
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
