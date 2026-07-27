"use client";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchPipeline } from "@/lib/api";
import type { PipelineDoc } from "@/components/workflow";
import { useWorkspace } from "@/lib/workspace";
import { isSelfOptimization } from "@/lib/derivations";
import { useChampionRegistry } from "@/lib/hooks/useChampionRegistry";
import { DashSpine } from "./DashSpine";
import { CyclePicker } from "@/components/shell/CyclePicker";
import { RunErrorBanner } from "./RunErrorBanner";
import { TopStrip } from "./TopStrip";
import { TimeRay } from "./TimeRay";
import { NowTriad } from "./NowTriad";
import { Lane } from "./Lane";
import { LiveStateCard } from "@/components/dashboard/scoring/LiveStateCard";
import { OuterVerdictPanel } from "@/components/dashboard/scoring/OuterVerdictPanel";
import { ChampionConsole } from "@/components/dashboard/scoring/ChampionConsole";
import { MechanismsPanel } from "@/components/dashboard/control/MechanismsPanel";
import { AllowedModelsPanel } from "@/components/dashboard/control/AllowedModelsPanel";
import { ConfigMapPanel } from "@/components/dashboard/control/ConfigMapPanel";

// The Dashboard tab's arrangement, owned here rather than inline in the shell:
// AppShell stays a thin tab-router + provider stack. Every section reads its
// own state from context (`useDashboard`/`useWorkspace`/`useSelection`); the
// only thing threaded is the one-shot pipeline topology, fetched here because
// it has no context home (a static read, not live state).
export function DashboardTab() {
  // One-shot pipeline (topology) lookup. Errors → pipeline stays null (the
  // canvas renders its own empty state); no retry needed for a static read.
  const { data: pipeline } = useFetch<PipelineDoc>(
    () => fetchPipeline().then((p) => p as PipelineDoc),
    [],
  );

  // The outer L4 loop earns extra boxes: the per-round outer verdict + the
  // cross-cycle champion/capability surfaces. `campaignId` is the ROOT hop, so
  // this reads the outer campaign's backend_type; depth 1 == viewing the outer
  // (not a drilled-in inner). Drilling into `↻ inner` → 2-hop path → gate false
  // → the plain dashboard, which is what an inner run should show. No fallback for
  // an unresolved backend_type — it correctly reads as "not self-optimizing".
  const { viewedPath, campaignId, campaigns } = useWorkspace();
  const rootBackendType = campaigns.find((c) => c.campaign_id === campaignId)?.backend_type;
  const isOuterSelfOpt = viewedPath?.length === 1 && isSelfOptimization(rootBackendType);
  // Fetch fires only when gated in (one-shot, not the 2 s poll).
  const { registry } = useChampionRegistry(isOuterSelfOpt);

  return (
    <div className="content" id="content-dashboard">
      <DashSpine>
        <header className="dash-hero">
          <div className="page-header">
            <div className="breadcrumb">
              Campaign » <CyclePicker />
            </div>
          </div>
        </header>
      </DashSpine>
      <DashSpine>
        <RunErrorBanner />
        <TopStrip />
      </DashSpine>
      {isOuterSelfOpt && (
        <DashSpine>
          <OuterVerdictPanel />
        </DashSpine>
      )}
      {/* Full-bleed, and deliberately OUTSIDE DashSpine: the spine is a 980px stripe that
          also translates left by half the sidebar width, so a chronology inside it reads as
          a widget rather than an axis. It sits above the optimizer + candidates because it
          answers the question you ask first — what happened, in order. */}
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
        id="mechanisms"
        title="Mechanisms"
        subtitle="Pluggable sorting + early-abort toggles (campaign.json)"
        defaultOpen={false}
      >
        <DashSpine>
          <MechanismsPanel />
        </DashSpine>
      </Lane>
      <Lane
        id="allowed-models"
        title="Allowed models"
        subtitle="Which models a human may steer a fork to without tainting it (campaign.json)"
        defaultOpen={false}
      >
        <DashSpine>
          <AllowedModelsPanel />
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
      {isOuterSelfOpt && (
        <Lane
          id="l4-lab"
          title="L4 lab"
          subtitle="Champion ranking — outer meta-optimization (dev surface)"
          defaultOpen={false}
        >
          <DashSpine>
            {registry ? (
              <ChampionConsole registry={registry} />
            ) : (
              <p className="l4-empty">Loading champion registry…</p>
            )}
          </DashSpine>
        </Lane>
      )}
    </div>
  );
}
