"use client";
import { useCallback } from "react";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { useConnector } from "@/lib/hooks/useConnector";
import { runSummaryFacts, type RunSummary } from "@/lib/derivations";
import { cx } from "@/lib/cx";
import { SubjectBox } from "@/components/shell/searchpoint/SubjectBox";
import { SummaryBlock } from "@/components/shell/SummaryBlock";
import { HardSamplesPreview } from "@/components/chat/HardSamplesPreview";
import { HardSamplesHeatmap } from "@/components/dashboard/samples/HardSamplesHeatmap";
import { RoundAxis } from "@/components/dashboard/pipeline/RoundAxis";
import { TrendChart } from "@/components/dashboard/scoring/TrendChart";

export function RunCard() {
  const { dash, isLive } = useCycleStream();
  const cv = useConnector();
  const { openView } = useWorkspace();
  const onOpenDashboard = useCallback(() => openView("dashboard"), [openView]);

  if (!dash?.cycle_id || (!isLive && dash.round_axis.completed.length === 0)) return null;

  return (
    <section className={cx("run-card", isLive && "is-live")} aria-label="This run" role="region">
      <div className="run-card-axis">
        <RoundAxis trailing={<TrendChart density="glyph" onOpen={onOpenDashboard} />} />
        {!cv.selfOptimization && <HardSamplesHeatmap />}
      </div>
      <div className="run-card-section">
        <SubjectBox />
      </div>
      {!cv.selfOptimization && (
        <div className="run-card-section">
          <HardSamplesPreview />
        </div>
      )}
    </section>
  );
}

export function RunSummaryItem({ summary }: { summary: RunSummary }) {
  return (
    <div className="chat-msg ai run-summary-item" role="note">
      <SummaryBlock dense facts={runSummaryFacts(summary)} />
      {summary.nextStep ? <p className="run-summary-next">{summary.nextStep}</p> : null}
      {summary.changes ? <p className="run-summary-changes">{summary.changes}</p> : null}
    </div>
  );
}
