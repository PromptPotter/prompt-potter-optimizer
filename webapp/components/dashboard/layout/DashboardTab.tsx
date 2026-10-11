"use client";
import { CardFrame } from "@/components/ui";
import { useConnector } from "@/lib/hooks/useConnector";
import { useDraftAuthoring } from "@/lib/hooks/useDraftAuthoring";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useCandidatesState } from "@/lib/candidates-store";
import { useHardSamples } from "@/lib/hard-samples";
import { useSelection } from "@/lib/SelectionContext";
import { useCampaign } from "@/lib/registry";
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
import { SubjectBox } from "@/components/shell/searchpoint/SubjectBox";

export function DashboardTab() {
  const cv = useConnector();
  const { doc: optimizerPipeline } = useOptimizerPipeline(cv.optimizer);
  const { datasetName } = useHardSamples();
  const authoring = useDraftAuthoring();
  const { candidate, node, setSelectionForCandidate, setSelectionForNode } = useSelection();
  const { showForest } = useCandidatesState();

  const { viewedPath, campaignId } = useWorkspace();
  const rootSelfOpt = useCampaign(campaignId)?.self_optimization;
  const isOuterSelfOpt = viewedPath?.length === 1 && rootSelfOpt === true;
  // An L4 unit has no cache.json roster — its samples ARE the inner campaigns.
  const hasSamples = !cv.selfOptimization;

  return (
    <div className="content" id="content-dashboard">
      <div className="box-grid">
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

        <CardFrame title="Leader" headingTag="h2">
          <SubjectBox />
        </CardFrame>
        <CandidatesCard />
        {showForest && <ForestCard />}
        {candidate && (
          <div className="card card-full inspector-card">
            <ScoringInspector selected={candidate} onClose={() => setSelectionForCandidate(null)} />
          </div>
        )}

        {hasSamples && (
          <CardFrame title="Samples" headingTag="h2" span="full">
            <HardSamplesHeatmap />
          </CardFrame>
        )}

        <CardFrame title="Pipeline" headingTag="h2">
          <PipelineStack datasetName={datasetName} />
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
