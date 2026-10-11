"use client";

import { phaseIs, phaseWalks } from "@/lib/run-phase";
import { panelCellLabel, pathOf } from "@/lib/derivations";
import type { CourseNode } from "@/lib/api";
import { PairedLift } from "@/components/shell/PairedLift";

export function PanelCellRow({
  cell,
  run,
  cached,
  onOpen,
}: {
  cell: string;
  run: CourseNode | null;
  cached: boolean;
  onOpen: (run: CourseNode) => void;
}) {
  const name = panelCellLabel(cell);

  // A live round's rows carry an empty `query` until the round file lands: unnamed = pending.
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

  const live = phaseWalks(run.run_phase);
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
        {phaseIs(run.run_phase, "settled") && (
          <span className="pcr-status">{run.status.label}</span>
        )}
        <span className="pcr-score">
          {run.run_standing ? <PairedLift reading={run.run_standing.vs_origin} /> : "—"}
        </span>
      </span>
    </button>
  );
}
