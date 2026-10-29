"use client";

import type { DraftCampaignWire } from "@/lib/api";
import { PLACEHOLDER } from "@/lib/format";

// A check-in has no `datasets/{slug}/` until Start, so `/cells` cannot answer.
export function DatasetPreview({ draft }: { draft: DraftCampaignWire }) {
  const { sample_preview: rows, headers } = draft;
  if (rows.length === 0 || headers.length === 0) return null;

  // `column_query` is `""` until confirmed, so an unnamed header must not match it.
  const roleOf = (h: string) =>
    !h ? "" : h === draft.column_query ? " (input)" : h === draft.column_ground_truth ? " (target)" : "";

  return (
    <div className="ds-preview-scroll">
      <table className="ds-preview-table">
        <caption>
          First {rows.length} of {draft.n_samples.toLocaleString()} rows
        </caption>
        <thead>
          <tr>
            {headers.map((h) => (
              <th key={h} scope="col">
                {h}
                {roleOf(h)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {headers.map((h) => {
                // NOT `?? ""`: a ragged row lacks trailing keys, and a missing cell is not an empty value.
                const v = row[h];
                return (
                  <td key={h} title={v ?? "This row carries no value for this column."}>
                    {v ?? PLACEHOLDER}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
