"use client";
import { useCycleStream } from "@/lib/poll";
import { useConnector } from "@/lib/hooks/useConnector";
import { runSummary, runSummaryFacts, type RunSummary } from "@/lib/derivations";
import { stopReasonNextStep } from "@/lib/run-phase";
import { cx } from "@/lib/cx";
import { pressable } from "@/components/ui";
import { LeaderSummary } from "@/components/shell/searchpoint/LeaderSummary";
import { SummaryBlock } from "@/components/shell/SummaryBlock";
import { HardSamplesPreview } from "@/components/chat/HardSamplesPreview";
import { TrendChart } from "@/components/dashboard/scoring/TrendChart";

// The run card inside the chat thread: a miniature Trend (click opens the Dashboard) over boxes that
// each lead with a summary and keep the full thing one disclosure away. `RunSummaryItem` survives a `resume`.

interface Props {
  // From the chat's ONE EventSource — a second `useCycleEvents` would open a second stream.
  sampleOrder: number[] | null;
  onOpenDashboard: () => void;
}

export function RunCard({ sampleOrder, onOpenDashboard }: Props) {
  const { dash, isLive } = useCycleStream();
  const cv = useConnector();
  const summary = runSummary(dash);

  if (!summary || (summary.rounds === 0 && !isLive)) return null;

  return (
    <section className={cx("run-card", isLive && "is-live")} aria-label="This run" role="region">
      {/* A div, not a button: the `CardFrame` inside is flow content; `pressable` restores activation. */}
      <div
        className="run-box run-trend"
        {...pressable(onOpenDashboard)}
        aria-label="Open the dashboard"
      >
        <TrendChart compact />
      </div>
      <LeaderSummary />
      {/* A pp-self outer cycle has no per-sample roster; the sidebar and L4 rows already `drillInto`. */}
      {!cv.selfOptimization && (
        <div className="run-box">
          <HardSamplesPreview sampleOrder={sampleOrder} />
        </div>
      )}
    </section>
  );
}

// The FROZEN thread item: captured VALUES, never a live read — a resume or `resume --from N` rewrites
// the files a live pane would re-read.
export function RunSummaryItem({ summary }: { summary: RunSummary }) {
  return (
    <div className="chat-msg ai run-summary-item" role="note">
      <SummaryBlock dense facts={runSummaryFacts(summary)} />
      {stopReasonNextStep(summary.stopReason) ? (
        <p className="run-summary-next">{stopReasonNextStep(summary.stopReason)}</p>
      ) : null}
      {summary.changes ? <p className="run-summary-changes">{summary.changes}</p> : null}
    </div>
  );
}
