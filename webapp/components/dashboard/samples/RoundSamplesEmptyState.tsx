"use client";
import type { StatusKind } from "@/lib/poll";

// The "no candidates" resting state inside RoundSamplesView. Distinguishes a
// live round still waiting on its first candidate from a completed round that
// carried none. Extracted from RoundSamplesView as a pure renderer.
export function RoundSamplesEmptyState({
  status,
  isLiveView,
  round,
}: {
  status: StatusKind;
  isLiveView: boolean;
  round: number;
}) {
  if (isLiveView && status === "live") {
    return (
      <div className="samples-empty">
        No candidates running yet this round. They&apos;ll appear here as
        the optimizer scores them.
      </div>
    );
  }
  return (
    <div className="samples-empty">
      Round {round} carries no candidates.
    </div>
  );
}
