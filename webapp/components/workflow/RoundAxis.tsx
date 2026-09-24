"use client";
import { availableRounds } from "@/lib/derivations";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";

// The round axis, in the optimizer card because its canvas depicts one round. Writes
// `selection.round`: `null` and `liveRound` both follow live.
export function RoundAxis() {
  const { dash, isLive } = useDashboard();
  const { round: selectedRound, setSelectionForRound } = useSelection();
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
