"use client";
import { CardFrame } from "@/components/ui";
import { useConnector } from "@/lib/hooks/useConnector";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useCandidatesState } from "@/lib/state/candidates";
import { useHardSamples } from "@/lib/hard-samples";
import { useSelection } from "@/lib/SelectionContext";
import { hasTag } from "@/lib/derivations";
import { useEntry } from "@/lib/state/registry";
import { useWorkspace } from "@/lib/workspace";
import { RunErrorBanner } from "./RunErrorBanner";
import { TopStrip } from "./TopStrip";
import { TimeRay } from "./TimeRay";
import { CandidatesCard } from "@/components/candidates/CandidatesCard";
import { ForestCard } from "@/components/candidates/ForestCard";
import { BackendRetries } from "@/components/dashboard/pipeline/BackendRetries";
import { BackendRevisionDrift } from "@/components/dashboard/pipeline/BackendRevisionDrift";
import { OptimizerCard } from "@/components/dashboard/pipeline/OptimizerCard";
import { PipelineStack } from "@/components/dashboard/pipeline/PipelineStack";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { CostStrip } from "@/components/dashboard/scoring/CostStrip";
import { FreqChart } from "@/components/dashboard/scoring/FreqChart";
import { OuterSignalPanel } from "@/components/dashboard/scoring/OuterSignalPanel";
import { TrendChart } from "@/components/dashboard/scoring/TrendChart";
import { ScoringInspector } from "@/components/dashboard/scoring/ScoringInspector";
import { NodeDetail } from "@/components/shell/node-surface/NodeDetail";
import { SubjectBox } from "@/components/shell/searchpoint/SubjectBox";

export function DashboardTab() {
  const cv = useConnector();
  const { doc: optimizerPipeline } = useOptimizerPipeline(cv.optimizer);
  const { datasetName } = useHardSamples();
  const { candidate, node, setSelectionForCandidate, setSelectionForNode } = useSelection();
  const { showForest } = useCandidatesState();

  const { viewedPath, campaignId } = useWorkspace();
  const rootSelfOpt = hasTag(useEntry(campaignId), "self_optimization");
  const isOuterSelfOpt = viewedPath?.length === 1 && rootSelfOpt;
  // An L4 unit has no sample roster — its samples ARE the inner campaigns.
  const hasSamples = !cv.selfOptimization;

  return (
    <div className="content" id="content-dashboard">
      <div className="box-grid">
        {/* Read top-down as the backbone: the run now → who leads this round → the rounds so far →
            the cells → the pipeline being tuned and the optimizer tuning it. */}
        <div className="box-full">
          <RunErrorBanner />
          <TopStrip />
        </div>
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
        {isOuterSelfOpt && <OuterSignalPanel />}

        <TrendChart />
        <CostStrip />

        {hasSamples && (
          <CardFrame title="Samples" headingTag="h2" span="full">
            <HardSamplesHeatmap />
            <div className="card-inset">
              <FreqChart />
            </div>
          </CardFrame>
        )}

        <CardFrame title="Pipeline" headingTag="h2">
          <PipelineStack datasetName={datasetName} />
          <BackendRevisionDrift />
          <BackendRetries />
        </CardFrame>
        <OptimizerCard pipeline={optimizerPipeline} />
        {node && (
          <div className="box-full">
            <NodeDetail node={node} onClose={() => setSelectionForNode(null)} />
          </div>
        )}
      </div>
    </div>
  );
}
