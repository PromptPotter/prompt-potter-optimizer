"use client";
import { useState } from "react";
import { fmtPct0 } from "@/lib/format";
import { phaseIs } from "@/lib/run-phase";
import { fitnessStyle } from "@/lib/derivations";
import { useHardSamples } from "@/lib/hard-samples";
import { useCycleStream } from "@/lib/poll";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";
import { SampleTrajectory, SampleTrajectoryMiniButton } from "./SampleTrajectory";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

export function HardSamplesHeatmap() {
  const {
    datasetName,
    items: datasetItems,
    totals: datasetTotals,
    stale: datasetStale,
    error: datasetError,
  } = useHardSamples();
  const { dash } = useCycleStream();
  const [heatExpanded, setHeatExpanded] = useState(false);
  const [bankExpanded, setBankExpanded] = useState(false);

  // A check-in 404s `/cells` by construction (`datasets/{slug}/` is written at Start): not a broken dataset.
  const rosterNote = datasetError
    ? `Couldn’t read this campaign’s samples${datasetName ? ` (${datasetName})` : ""}.`
    : datasetItems.length > 0
      ? null
      : phaseIs(dash?.run_phase, "authoring")
        ? "Not committed yet — the sample bank is written when this campaign starts."
        : datasetStale
          ? "Loading this campaign’s samples…"
          : "No samples on this campaign’s dataset yet.";

  const outcome =
    datasetTotals && datasetTotals.mean_fitness != null
      ? ` · ${datasetTotals.total_measurements} measurements · ${fmtPct0(datasetTotals.mean_fitness)} mean fitness`
      : "";
  const summary = `${datasetName ? `${datasetName} · ` : ""}${datasetItems.length} samples${outcome}`;

  return (
    <div className="hs-heat-wrap">
      {/* The row renders without a roster: `SampleTrajectoryMiniButton` reads `dash.rounds`. */}
      <div className="hs-controls-row">
        {rosterNote ? (
          <p className="hs-heat-empty" role="status">
            {rosterNote}
          </p>
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
                    key={it.sample_id}
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
          {heatExpanded && !rosterNote && (
            <div className="hs-expand-wrap">
              <MeasurementsPane preset={{ groupBy: "sample" }} />
            </div>
          )}
        </RotatePrompt>
      )}
    </div>
  );
}
