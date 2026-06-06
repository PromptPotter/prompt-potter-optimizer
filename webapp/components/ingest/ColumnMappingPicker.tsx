"use client";

import type { DraftCampaignWire, DraftPatch, ProvenanceTag } from "@/lib/api";
import { ORIGIN_KEY } from "@/lib/origin-readiness";
import { ProvenanceBadge } from "./ProvenanceBadges";

// The required tier: pick which uploaded header is the input and which is the
// target. Selecting a column confirms it (rides `edit-draft-campaign` with
// `column_query` / `column_ground_truth`) — no separate Apply click, since
// the pick *is* the confirmation.
export function ColumnMappingPicker({
  draft,
  onApply,
}: {
  draft: DraftCampaignWire;
  onApply: (patch: DraftPatch) => void;
}) {
  if (draft.headers.length === 0) {
    return (
      <p className="new-campaign-error">
        No columns were detected in the upload. Re-upload a CSV with a header row.
      </p>
    );
  }

  const queryProv: ProvenanceTag = draft.field_provenance[ORIGIN_KEY.columnQuery] ?? "unset";
  const gtProv: ProvenanceTag = draft.field_provenance[ORIGIN_KEY.columnGroundTruth] ?? "unset";
  const sameColumn =
    !!draft.column_query && draft.column_query === draft.column_ground_truth;

  return (
    <div className="origin-columns">
      <span className="origin-columns-head">Map your columns</span>
      <div className="origin-column-row">
        <label className="new-campaign-field">
          <span>
            Input column <ProvenanceBadge tag={queryProv} />
          </span>
          <select
            value={draft.column_query}
            onChange={(e) => onApply({ column_query: e.target.value })}
          >
            <option value="" disabled>
              — pick a column —
            </option>
            {draft.headers.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
        </label>
        <label className="new-campaign-field">
          <span>
            Target column <ProvenanceBadge tag={gtProv} />
          </span>
          <select
            value={draft.column_ground_truth}
            onChange={(e) => onApply({ column_ground_truth: e.target.value })}
          >
            <option value="" disabled>
              — pick a column —
            </option>
            {draft.headers.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
        </label>
      </div>
      {sameColumn ? (
        <small className="new-campaign-warn">
          Input and target are the same column — every answer will trivially
          match. Pick two different columns.
        </small>
      ) : null}
    </div>
  );
}
