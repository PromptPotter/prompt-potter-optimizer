"use client";
// One cell of an L4 panel, rendered as the inner campaign it is: an outer round records
// `is_hit: null` for a cell, which was optimized by a whole campaign rather than scored.

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

  // A live round's rows carry an empty `query` until the round file lands: an unnamed cell is
  // pending, while a named one with no run is genuinely unrecorded.
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
          <span className="pcr-status">{runPhaseLabel(run.run_phase, run.status)}</span>
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
