"use client";
// ONE cell of an L4 panel — rendered as the inner campaign it actually is.
//
// The plain `SampleRowItem` cannot say anything true about these: an outer round
// records `is_hit: null`, no prediction and no ground truth for a cell, because
// the cell was not scored — it was OPTIMIZED, by a whole campaign. Rendered as a
// scored row it read as a bare query string that had missed.
//
// So the row shows what the run did (its phase, its origin → best) and opens it, the
// same `drillInto` hop the sidebar's inner rows fire. A cell whose run is absent still
// gets a row: the outer round measured it, and saying so is honest where inventing a
// run is not — see the two absent states below, which are not the same absence.

import { fmtPct0 } from "@/lib/format";
import { runPhaseLabel } from "@/lib/run-phase";
import { panelCellLabel, pathOf } from "@/lib/derivations";
import type { LineageNode } from "@/lib/api";

export function PanelCellRow({
  cell,
  run,
  cached,
  onOpen,
}: {
  cell: string;
  run: LineageNode | null;
  cached: boolean;
  onOpen: (run: LineageNode) => void;
}) {
  const name = panelCellLabel(cell);

  // The LIVE round names no cell: its in-flight rows carry the sample's result and an
  // empty `query`, and the cell's name only lands when the round file is written. With
  // no name there is no join, so every cell of a live L4 round read "run not recorded"
  // — which accuses the engine of losing provenance when it has simply not finished
  // saying it yet. An unnamed cell is pending; a named one with no run is not.
  if (!cell) {
    return (
      <div className="rsv-row pcr-row pcr-absent">
        <span className="pcr-name">cell</span>
        <span
          className="pcr-note"
          title="The round is still open. A cell is named in the round file, which lands when the round closes — until then there is nothing to join its inner campaign on."
        >
          pending
        </span>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="rsv-row pcr-row pcr-absent">
        <span className="pcr-name">{name}</span>
        <span
          className="pcr-note"
          title="The outer round scored this cell, but no inner campaign in the sandbox carries a `spawned_by` stamp for it — an interrupted mint writes no provenance. It is not placed by guess."
        >
          run not recorded
        </span>
      </div>
    );
  }

  const live = run.run_phase === "running";
  const lifted =
    run.origin_accuracy != null && run.best_accuracy != null && run.best_accuracy !== run.origin_accuracy;
  const at = pathOf(run).at(-1);

  return (
    <button
      type="button"
      className="rsv-row pcr-row"
      onClick={() => onOpen(run)}
      title={`${at?.campaignId ?? ""} · ${at?.cycleId ?? ""}\n\nThe inner campaign that measured this cell. Opens it.`}
    >
      <span className="pcr-name">
        {name}
        {live && (
          <span className="unit-library-live" title="Status is running">
            ●
          </span>
        )}
        {cached && (
          <span
            className="tag-cached"
            title="Reused from a prior identical searchpoint — this cell cost no fresh run"
          >
            📖
          </span>
        )}
      </span>
      <span className="pcr-meta">
        {run.run_phase === "terminal" && (
          <span className="pcr-status">{runPhaseLabel(run.run_phase, run.state)}</span>
        )}
        <span className="pcr-score">
          {fmtPct0(run.origin_accuracy ?? run.best_accuracy ?? null)}
          {lifted && (
            <>
              <span className="unit-library-arrow" aria-label="improved to">
                →
              </span>
              {fmtPct0(run.best_accuracy)}
            </>
          )}
        </span>
      </span>
    </button>
  );
}
