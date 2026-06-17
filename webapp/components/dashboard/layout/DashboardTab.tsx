"use client";
import { useFetch } from "@/lib/hooks/useFetch";
import { fetchPipeline } from "@/lib/api";
import type { PipelineDoc } from "@/components/workflow";
import { DashSpine } from "./DashSpine";
import { CyclePicker } from "@/components/shell/CyclePicker";
import { RunErrorBanner } from "./RunErrorBanner";
import { TopStrip } from "./TopStrip";
import { RoundTabsStrip } from "@/components/dashboard/samples/RoundTabsStrip";
import { NowTriad } from "./NowTriad";
import { Lane } from "./Lane";
import { LiveStateCard } from "@/components/dashboard/scoring/LiveStateCard";

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
        {/* TopStrip + RoundTabsStrip share one row so the round axis (LIVE
            pill + completed-round circles) sits beside the headline KPIs the
            operator scans first. */}
        <div className="dash-top-row">
          <TopStrip />
          <RoundTabsStrip />
        </div>
      </DashSpine>
      <DashSpine>
        <NowTriad pipeline={pipeline} />
      </DashSpine>
      <Lane
        id="livestate"
        title="Live state"
        subtitle="Raw dashboard.json + trend + score frequency"
        defaultOpen
      >
        <DashSpine>
          <LiveStateCard />
        </DashSpine>
      </Lane>
    </div>
  );
}
