"use client";

import { panelCellLabel, phaseIs, phaseWalks } from "@/lib/derivations";
import { pathOfAddress } from "@/lib/ids";
import type { HierarchyCourse } from "@/lib/api";
import { PairedLift } from "@/components/shell/PairedLift";
import { PLACEHOLDER } from "@/lib/format";

export function PanelCellRow({
  cell,
  run,
  cached,
  onOpen,
}: {
  cell: string;
  run: HierarchyCourse | null;
  cached: boolean;
  onOpen: (run: HierarchyCourse) => void;
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

  const { state, facts } = run;
  const live = phaseWalks(state.run_phase);
  const at = pathOfAddress(run.address).at(-1);

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
        {phaseIs(state.run_phase, "settled") && (
          <span className="pcr-status">{state.status.label}</span>
        )}
        <span className="pcr-score">
          {facts.standing ? <PairedLift reading={facts.standing.vs_origin} /> : PLACEHOLDER}
        </span>
      </span>
    </button>
  );
}
