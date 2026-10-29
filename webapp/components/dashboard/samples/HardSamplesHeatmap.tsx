"use client";
import { useState } from "react";
import { fmtUnit } from "@/lib/format";
import { fitnessStyle } from "@/lib/derivations";
import { useHardSamples } from "@/lib/hard-samples";
import { useCycleStream } from "@/lib/poll";
import { MeasurementsPane } from "@/components/measurements/MeasurementsPane";
import { RosterNote } from "@/components/measurements/RosterNote";
import { SampleTrajectory, SampleTrajectoryMiniButton } from "./SampleTrajectory";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

export function HardSamplesHeatmap() {
  const {
    datasetName,
    items: datasetItems,
    totals: datasetTotals,
    failure: datasetFailure,
  } = useHardSamples();
  const { dash } = useCycleStream();
  const [heatExpanded, setHeatExpanded] = useState(false);
  const [bankExpanded, setBankExpanded] = useState(false);
  const rosterShown = datasetFailure === null && datasetItems.length > 0;

  const outcome =
    datasetTotals && datasetTotals.mean_fitness != null
      ? ` · ${datasetTotals.total_measurements} measurements · ${fmtUnit("rate", datasetTotals.mean_fitness)} mean fitness`
      : "";
  const summary = `${datasetName ? `${datasetName} · ` : ""}${datasetItems.length} samples${outcome}`;

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
            onClick={() => setHeatExpanded((e) => !e)}
            aria-expanded={heatExpanded}
            aria-label={
              heatExpanded ? "Collapse the sample leaderboard" : `Expand the sample leaderboard. ${summary}.`
            }
            title={`${summary} - click to ${heatExpanded ? "collapse" : "expand"}`}
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
      {(bankExpanded || heatExpanded) && (
        <RotatePrompt surfaceName="The sample heat-map">
          {bankExpanded && <SampleTrajectory rounds={dash?.rounds ?? []} />}
          {heatExpanded && rosterShown && (
            <div className="hs-expand-wrap">
              <MeasurementsPane preset={{ groupBy: "sample" }} />
            </div>
          )}
        </RotatePrompt>
      )}
    </div>
  );
}
