"use client";
import { roundOf, type DashboardSnapshot, type StatusKind } from "@/lib/poll";
import { CardFrame } from "@/components/ui/card";
import { useSelection } from "@/components/dashboard/SelectionContext";
import { LiveTape } from "./LiveTape";
import { RoundSamplesTable } from "./RoundSamplesTable";

interface Props {
  dash: DashboardSnapshot | null;
  status: StatusKind;
  campaignId: string | null;
  cycleId: string | null;
}

// Card body switches by the shared selection.round slot:
//   selection.round == null OR == live in-flight round → LiveTape
//   selection.round == a completed round              → RoundSamplesTable
//   no rounds at all                                  → empty hint
// One CardFrame, three bodies — keeps the title bar steady when the
// operator clicks between rounds so the page doesn't jump.
export function RoundSamplesCard({ dash, status, campaignId, cycleId }: Props) {
  const { round: selectedRound, setRound } = useSelection();
  const liveRound = roundOf(dash);
  // null = follow live: fall through to the in-flight round when one
  // exists. A completed-round pick stays explicit until the operator
  // clicks the live pill or another tab.
  const effectiveRound = selectedRound ?? liveRound;
  const isLiveView =
    effectiveRound != null && liveRound != null && effectiveRound === liveRound;
  const liveness = status === "live" ? "rolling" : "snapshot";

  const title =
    effectiveRound == null
      ? "Samples"
      : isLiveView
        ? `Samples · R${effectiveRound}`
        : `Round ${effectiveRound} · samples`;

  const actions =
    effectiveRound == null ? null : isLiveView ? (
      <span className="badge">{liveness}</span>
    ) : (
      <button
        type="button"
        className="badge round-back-live"
        onClick={() => setRound(null)}
        title="Follow the in-flight round again"
      >
        ← live
      </button>
    );

  return (
    <CardFrame
      className="round-samples-card"
      headingTag="h2"
      title={title}
      actions={actions}
    >
      {effectiveRound == null ? (
        <div className="samples-empty">
          No rounds yet. Samples appear here once the optimizer starts
          scoring — start it with{" "}
          <code>python -m promptpotter resume</code>.
        </div>
      ) : isLiveView ? (
        <LiveTape dash={dash} status={status} round={effectiveRound} />
      ) : (
        <RoundSamplesTable
          campaignId={campaignId}
          cycleId={cycleId}
          round={effectiveRound}
        />
      )}
    </CardFrame>
  );
}
