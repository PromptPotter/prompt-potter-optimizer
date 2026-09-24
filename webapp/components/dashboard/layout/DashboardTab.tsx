"use client";
import { useOptimizerPipeline } from "@/lib/hooks/useOptimizerPipeline";
import { useWorkspace } from "@/lib/workspace";
import { isSelfOptimization } from "@/lib/derivations";
import { DashSpine } from "./DashSpine";
import { RunErrorBanner } from "./RunErrorBanner";
import { TopStrip } from "./TopStrip";
import { TimeRay } from "./TimeRay";
import { NowTriad } from "./NowTriad";
import { Lane } from "./Lane";
import { LiveStateCard } from "@/components/dashboard/scoring/LiveStateCard";
import { OuterSignalPanel } from "@/components/dashboard/scoring/OuterSignalPanel";
import { ConfigMapPanel } from "@/components/dashboard/control/ConfigMapPanel";

// The Dashboard tab's arrangement; only the one-shot pipeline topology is threaded.
export function DashboardTab() {
  const { doc: pipeline } = useOptimizerPipeline();

  // `campaignId` is the ROOT hop and depth 1 is the outer view, so a drilled-in inner run gets
  // the plain dashboard.
  const { viewedPath, campaignId, campaigns } = useWorkspace();
  const rootBackendType = campaigns.find((c) => c.campaign_id === campaignId)?.backend_type;
  const isOuterSelfOpt = viewedPath?.length === 1 && isSelfOptimization(rootBackendType);

  return (
    <div className="content" id="content-dashboard">
      <DashSpine>
        <RunErrorBanner />
        <TopStrip />
      </DashSpine>
      {isOuterSelfOpt && (
        <DashSpine>
          <OuterSignalPanel />
        </DashSpine>
      )}
      {/* Full-bleed, outside DashSpine: inside the spine a chronology reads as a widget. */}
      <TimeRay />
      <DashSpine>
        <NowTriad pipeline={pipeline} />
      </DashSpine>
      <Lane
        id="livestate"
        title="2ndary-relevant-info"
        subtitle="Raw dashboard.json + trend + score frequency"
        defaultOpen
      >
        <DashSpine>
          <LiveStateCard />
        </DashSpine>
      </Lane>
      <Lane
        id="config-map"
        title="Config map"
        subtitle="What each knob moves, what overwrites what, and which knobs clash"
        defaultOpen={false}
      >
        <DashSpine>
          <ConfigMapPanel />
        </DashSpine>
      </Lane>
    </div>
  );
}
