"use client";
import { useCallback } from "react";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { useConnector } from "@/lib/hooks/useConnector";
import type { HierarchyCourse } from "@/lib/api";
import { cx } from "@/lib/cx";
import { entryLede, runNextStep } from "@/lib/derivations";
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
    <section className={cx("run-card", isLive && "is-live")} aria-label="This run">
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

// The course entry as served when its producer detached: frozen, never re-worded here.
export function RunSummaryItem({ course }: { course: HierarchyCourse }) {
  const changes = course.facts.standing?.changes;
  const lede = entryLede(course);
  const nextStep = runNextStep(course.state.status);
  return (
    <div className="chat-msg ai run-summary-item" role="note">
      <SummaryBlock
        dense
        facts={course}
        lede={lede ? <p className="run-summary-line">{lede}</p> : null}
      />
      {nextStep ? <p className="run-summary-line">{nextStep}</p> : null}
      {changes ? <p className="run-summary-line">{changes}</p> : null}
      {course.caveat ? <p className="run-summary-line">{course.caveat}</p> : null}
    </div>
  );
}
