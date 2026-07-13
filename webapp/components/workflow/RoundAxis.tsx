"use client";
import { availableRounds } from "@/lib/derivations";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";

// The round axis, in the toolbar of the card it scopes. One circle per L1 round,
// plus a LIVE pill when an in-flight round exists that hasn't been summarized
// into `dash.rounds[]` yet.
//
// It lives HERE because the optimizer canvas can only ever depict one round — so
// "which round" is not a free-floating dashboard axis, it is this card's scope.
// Standing apart as its own panel is what let the canvas drift into showing live
// node state under a historical round's node detail.
//
// Click writes `selection.round`; the candidates card and the samples view react
// to that same single source of truth.
//
// Selection semantics:
//   round === null         → follow live (default, no explicit pick)
//   round === liveRound    → explicit "show live" (same view as null)
//   round  <  liveRound    → drill into a completed round
export function RoundAxis() {
  const { dash, isLive } = useDashboard();
  const { round: selectedRound, setSelectionForRound } = useSelection();
  // Single round-axis truth — `completed` circles + the `live` pill, the
  // latter already gated on `isLive` so a stopped run drops the pill.
  const { completed, live: liveRound } = availableRounds(dash, isLive);
  const liveActive = liveRound != null;
  const followingLive =
    liveActive && (selectedRound == null || selectedRound === liveRound);

  if (completed.length === 0 && !liveActive) return null;

  return (
    <div className="round-axis" role="tablist" aria-label="L1 rounds">
      <span className="round-axis-label">Round</span>
      <div className="round-axis-scroll">
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
              title={`Show round ${r} — its candidates, its optimizer nodes, its samples`}
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
