"use client";
import { type DashboardSnapshot } from "@/lib/poll";
import { availableRounds } from "@/lib/derivations/round-axis";
import { useSelection } from "@/components/dashboard/SelectionContext";

interface Props {
  dash: DashboardSnapshot | null;
  isLive: boolean;
}

// One circle per L1 round, plus a LIVE pill when an in-flight round exists
// that hasn't been summarized into `dash.rounds[]` yet. Click writes
// `selection.round`; the lineage tree, fitness chart, and samples card
// all react to that single source of truth.
//
// Selection semantics:
//   round === null         → follow live (default, no explicit pick)
//   round === liveRound    → explicit "show live" (same view as null)
//   round  <  liveRound    → drill into a completed round
export function RoundTabsStrip({ dash, isLive }: Props) {
  const { round: selectedRound, setSelectionForRound } = useSelection();
  // Single round-axis truth — `completed` circles + the `live` pill, the
  // latter already gated on `isLive` so a stopped run drops the pill.
  const { completed, live: liveRound } = availableRounds(dash, isLive);
  const liveActive = liveRound != null;
  const followingLive =
    liveActive && (selectedRound == null || selectedRound === liveRound);

  if (completed.length === 0 && !liveActive) return null;

  return (
    <div className="round-tabs-strip" role="tablist" aria-label="L1 rounds">
      <span className="round-tabs-label">Round</span>
      <div className="round-tabs-scroll">
        {completed.map((r) => {
          const active = selectedRound === r;
          return (
            <button
              key={r}
              type="button"
              role="tab"
              aria-selected={active}
              className={`round-tab${active ? " active" : ""}`}
              onClick={() => setSelectionForRound(r)}
              title={`Show round ${r} samples`}
            >
              {r}
            </button>
          );
        })}
        {liveActive && (
          <button
            type="button"
            role="tab"
            aria-selected={followingLive}
            className={`round-tab round-tab-live${followingLive ? " active" : ""}`}
            onClick={() => setSelectionForRound(null)}
            title="Follow the in-flight round"
          >
            <span className="round-tab-live-dot" aria-hidden />
            R{liveRound} · live
          </button>
        )}
      </div>
    </div>
  );
}
