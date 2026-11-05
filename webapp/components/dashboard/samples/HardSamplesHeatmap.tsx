import { useState } from "react";
import { fitnessStyle } from "@/lib/derivations";
import { useCells } from "@/lib/hooks/useCells";
import { failureOf, shownData } from "@/lib/hooks/useRead";
import { useViewedDashboard } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { RosterNote } from "@/components/measurements/RosterNote";
import { SampleTrajectory, SampleTrajectoryMiniButton } from "./SampleTrajectory";

export function HardSamplesHeatmap() {
  const { read } = useCells({});
  const samples = shownData(read)?.samples ?? [];
  const { dash } = useViewedDashboard();
  const { open } = useWorkspace();
  const [bankExpanded, setBankExpanded] = useState(false);
  const rosterShown = failureOf(read) === null && samples.length > 0;

  return (
    <div className="hs-heat-wrap">
      {/* The row renders without a roster: `SampleTrajectoryMiniButton` reads `dash.rounds`. */}
      <div className="hs-controls-row">
        {!rosterShown ? (
          <RosterNote />
        ) : (
          <button
            type="button"
            className="hs-mini-btn resizable"
            onClick={() => open("samples")}
            aria-label="Open the Samples leaderboard"
          >
            <span className="hs-mini-tiles" aria-hidden="true">
              {samples.map((it) => {
                const mean = it.mean_fitness ?? null;
                return (
                  <span
                    key={it.position}
                    className="hs-mini-cell"
                    style={mean == null ? undefined : fitnessStyle(mean)}
                  />
                );
              })}
            </span>
          </button>
        )}
        <SampleTrajectoryMiniButton
          expanded={bankExpanded}
          rounds={dash?.rounds ?? []}
          onToggle={() => setBankExpanded((e) => !e)}
        />
      </div>
      {bankExpanded && <SampleTrajectory rounds={dash?.rounds ?? []} />}
    </div>
  );
}
