"use client";
import { useState } from "react";
import { fitnessStyle } from "@/lib/derivations";
import { useHardSamples } from "@/lib/hard-samples";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { RosterNote } from "@/components/measurements/RosterNote";
import { SampleTrajectory, SampleTrajectoryMiniButton } from "./SampleTrajectory";

export function HardSamplesHeatmap() {
  const { items: datasetItems, failure: datasetFailure } = useHardSamples();
  const { dash } = useCycleStream();
  const { open } = useWorkspace();
  const [bankExpanded, setBankExpanded] = useState(false);
  const rosterShown = datasetFailure === null && datasetItems.length > 0;

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
              {datasetItems.map((it) => {
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
