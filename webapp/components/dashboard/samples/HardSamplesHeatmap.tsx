"use client";
import { useState } from "react";
import { fmtPct0 } from "@/lib/format";
import { fitnessStyle } from "@/lib/derivations";
import { useHardSamples } from "@/lib/hard-samples";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";
import { SampleTrajectory, SampleTrajectoryMiniButton } from "./SampleTrajectory";
import { RotatePrompt } from "@/components/shell/RotatePrompt";

// Hard-samples heat-map: a compact badge - one tile per sample in the served ranking, shaded
// by its served mean fitness, dark = no measurements. Clicking it unfolds the leaderboard in
// place - the one measurement log, preset to group by sample; the round in flight is already
// in the served rows, merged once on the server.
export function HardSamplesHeatmap() {
  const {
    datasetName,
    items: datasetItems,
    // Served roster-wide totals for the scope in view - never folded down from the tiles.
    totals: datasetTotals,
    stale: datasetStale,
    error: datasetError,
  } = useHardSamples();
  const { dash } = useDashboard();
  const [heatExpanded, setHeatExpanded] = useState(false);
  const [bankExpanded, setBankExpanded] = useState(false);

  // Tile order is the order `/cells` SERVED — `items[i].hard_sample_rank === i + 1`
  // (`datasets.py`), so `datasetItems` IS the ranking and nothing here arranges it. Two
  // hand-written comparators lived here and in the table; sorting on the served rank
  // instead only made the re-derivation agree with itself. An ordering is a score: the
  // fix is not to sort it correctly, it is not to sort it.

  // FOUR facts, four sentences, and none of them replaces the control row. A failed
  // read and an empty roster were split first (both used to `return null`, so a 404
  // was indistinguishable from a collapsed panel); STILL LOADING came next, because
  // the roster is four `limit=1000` reads and for their whole duration this panel
  // asserted the campaign had no samples. The fourth is a CHECK-IN: `datasets/{slug}/`
  // is written at Start, so `/cells` 404s by construction, and "no samples yet" reads
  // as a broken dataset when nothing is broken. The rows for that state are on the
  // draft, rendered by the check-in panel below this hero.
  const rosterNote = datasetError
    ? `Couldn’t read this campaign’s samples${datasetName ? ` (${datasetName})` : ""}.`
    : datasetItems.length > 0
      ? null
      : dash?.run_phase === "checkin"
        ? "Not committed yet — the sample bank is written when this campaign starts."
        : datasetStale
          ? "Loading this campaign’s samples…"
          : "No samples on this campaign’s dataset yet.";

  // Mean fitness, not a hit rate: on a binary scorer the two are the same number
  // (the mean of 0/1 IS the hit rate), and on a graded one only this reports
  // anything — the hit ceiling is unreachable there, which is what made the old
  // "0/N hit" headline read as a dead pipeline on a working campaign.
  const outcome =
    datasetTotals && datasetTotals.mean_fitness != null
      ? ` · ${datasetTotals.total_measurements} measurements · ${fmtPct0(datasetTotals.mean_fitness)} mean fitness`
      : "";
  const summary = `${datasetName ? `${datasetName} · ` : ""}${datasetItems.length} samples${outcome}`;

  return (
    <div className="hs-heat-wrap">
      {/* ONE control row, whatever the roster says. `SampleTrajectoryMiniButton` reads
          `dash.rounds`, not the roster, so a roster read has no business hiding it — and
          it did, because each state above used to replace the whole row with a sentence. */}
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
              {/* The SERVED per-sample mean, shaded by the one `fitnessStyle` — the colour the
                  same sample wears in Measurements. A gradient, never a threshold: two arms of a
                  graded scorer must not land on one flat colour. */}
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
