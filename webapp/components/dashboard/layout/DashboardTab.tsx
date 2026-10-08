"use client";
import { useState } from "react";
import { CardFrame } from "@/components/ui";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDraftAuthoring } from "@/lib/hooks/useDraftAuthoring";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useCandidatesState } from "@/lib/candidates-store";
import { useHardSamples } from "@/lib/hard-samples";
import { useSelection } from "@/lib/SelectionContext";
import { useWorkspace } from "@/lib/workspace";
import { RunErrorBanner } from "./RunErrorBanner";
import { TopStrip } from "./TopStrip";
import { TimeRay } from "./TimeRay";
import { Lane } from "./Lane";
import { CandidatesCard } from "@/components/candidates/CandidatesCard";
import { ForestCard } from "@/components/candidates/ForestCard";
import { ConfigMapPanel } from "@/components/dashboard/pipeline/ConfigMapPanel";
import { OptimizerCard } from "@/components/dashboard/pipeline/OptimizerCard";
import { PipelineStack } from "@/components/dashboard/pipeline/PipelineStack";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { LiveStateCard } from "@/components/dashboard/scoring/LiveStateCard";
import { OuterSignalPanel } from "@/components/dashboard/scoring/OuterSignalPanel";
import { ScoringInspector } from "@/components/dashboard/scoring/ScoringInspector";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { LeaderSummary } from "@/components/shell/searchpoint/LeaderSummary";

// The Dashboard: ONE grid of boxes, read top-down by the level each box is about — the cycle's
// status, its leader, the round's candidates, their samples, then the pipeline that produced
// them. A box answers one level and nests nothing from another; what a click OPENS (a candidate's
// inspector, a node's detail) lands as its own box directly under the box it was clicked in.
// Sizing is the grid's alone (`.box-grid`, panels.css): no box here states a width.
export function DashboardTab() {
  const cv = useConnector();
  const { doc: optimizerPipeline } = useOptimizerPipeline(cv.optimizer);
  const { datasetName } = useHardSamples();
  const authoring = useDraftAuthoring();
  const { candidate, node, setSelectionForCandidate, setSelectionForNode } = useSelection();
  const { showForest } = useCandidatesState();
  const [samplesOpen, setSamplesOpen] = useState(true);

  // `campaignId` is the ROOT hop and depth 1 is the outer view, so a drilled-in inner run gets
  // the plain dashboard.
  const { viewedPath, campaignId, campaigns } = useWorkspace();
  const rootSelfOpt = campaigns.find((c) => c.campaign_id === campaignId)?.self_optimization;
  const isOuterSelfOpt = viewedPath?.length === 1 && rootSelfOpt === true;
  // An L4 unit has no cache.json roster — its samples ARE the inner campaigns.
  const hasSamples = !cv.selfOptimization;

  return (
    <div className="content" id="content-dashboard">
      <div className="box-grid">
        {/* CYCLE — what state the run is in, and when. */}
        <div className="box-full">
          <RunErrorBanner />
          <TopStrip />
        </div>
        {isOuterSelfOpt && (
          <div className="box-full">
            <OuterSignalPanel />
          </div>
        )}
        <div className="box-full">
          <TimeRay />
        </div>

        {/* CANDIDATE — the leader against the origin, then the round it leads. */}
        <CardFrame title="Leader" headingTag="h2">
          <LeaderSummary />
        </CardFrame>
        <CandidatesCard />
        {/* Its own box: the forest shares no axis with the bars. */}
        {showForest && <ForestCard />}
        {candidate && (
          <div className="card card-full inspector-card">
            <ScoringInspector selected={candidate} onClose={() => setSelectionForCandidate(null)} />
          </div>
        )}

        {/* SAMPLE — which rows are hard, and for whom. */}
        {hasSamples && samplesOpen && (
          <CardFrame title="Samples" headingTag="h2" span="full">
            <HardSamplesHeatmap />
          </CardFrame>
        )}

        {/* PIPELINE — what ran: the target pipeline, the optimizer over it, one node opened. */}
        <CardFrame title="Pipeline" headingTag="h2">
          <PipelineStack
            datasetName={datasetName}
            samplesOpen={samplesOpen}
            onToggleSamples={() => setSamplesOpen((v) => !v)}
          />
        </CardFrame>
        <OptimizerCard pipeline={optimizerPipeline} />
        {node && (
          <div className="box-full">
            <NodeDetail
              node={node}
              authoring={authoring}
              onClose={() => setSelectionForNode(null)}
            />
          </div>
        )}

        <Lane
          id="livestate"
          title="Run details"
          subtitle="Raw dashboard.json + trend + score frequency"
          defaultOpen
        >
          <LiveStateCard />
        </Lane>
        <Lane
          id="config-map"
          title="Config map"
          subtitle="What each knob moves, what overwrites what, and which knobs clash"
          defaultOpen={false}
        >
          <ConfigMapPanel />
        </Lane>
      </div>
    </div>
  );
}
