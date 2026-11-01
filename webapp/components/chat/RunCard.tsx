"use client";
import { useCycleStream } from "@/lib/poll";
import { useConnector } from "@/lib/hooks/useConnector";
import type { HierarchyCourse } from "@/lib/api";
import { cx } from "@/lib/cx";
import { runNextStep } from "@/lib/derivations";
import { SummaryBlock } from "@/components/shell/SummaryBlock";
import { HardSamplesPreview } from "@/components/chat/HardSamplesPreview";

// The chat's one run block, the sample walk: the leader, the trend and the heatmap are the dashboard's.
export function RunCard() {
  const { dash, isLive } = useCycleStream();
  const cv = useConnector();

  if (cv.selfOptimization || !dash?.cycle_id) return null;
  if (!isLive && dash.round_axis.completed.length === 0) return null;

  return (
    <section className={cx("run-card", isLive && "is-live")} aria-label="This run">
      <HardSamplesPreview />
    </section>
  );
}

// The course entry as served when its producer detached: frozen, never re-worded here.
export function RunSummaryItem({ course }: { course: HierarchyCourse }) {
  const changes = course.facts.standing?.changes;
  const nextStep = runNextStep(course.state.status);
  return (
    <div className="chat-msg ai run-summary-item" role="note">
      <SummaryBlock dense entry={course} />
      {nextStep ? <p className="run-summary-line">{nextStep}</p> : null}
      {changes ? <p className="run-summary-line">{changes}</p> : null}
    </div>
  );
}
