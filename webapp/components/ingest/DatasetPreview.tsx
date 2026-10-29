"use client";

import { DataTable, type Column } from "@/components/ui";
import type { DraftCampaignWire } from "@/lib/api";
import { PLACEHOLDER } from "@/lib/format";

type PreviewRow = DraftCampaignWire["sample_preview"][number];

// A check-in has no `datasets/{slug}/` until Start, so `/cells` cannot answer.
export function DatasetPreview({ draft }: { draft: DraftCampaignWire }) {
  const { sample_preview: rows, headers } = draft;
  if (rows.length === 0 || headers.length === 0) return null;

  // `column_query` is `""` until confirmed, so an unnamed header must not match it.
  const roleOf = (h: string) =>
    !h ? "" : h === draft.column_query ? " (input)" : h === draft.column_ground_truth ? " (target)" : "";

  // Keyed by position: an unnamed header is `""`, and two can be.
  const columns: Column<PreviewRow>[] = headers.map((h, i) => ({
    id: String(i),
    label: `${h}${roleOf(h)}`,
    width: "minmax(10ch, 280px)",
    cell: (row) => {
      // NOT `?? ""`: a ragged row lacks trailing keys, and a missing cell is not an empty value.
      const v = row[h];
      return <span title={v ?? "This row carries no value for this column."}>{v ?? PLACEHOLDER}</span>;
    },
  }));

  return (
    <div className="ds-preview">
      <p className="ds-preview-caption">
        First {rows.length} of {draft.n_samples.toLocaleString()} rows
      </p>
      <DataTable
        ariaLabel="Dataset preview"
        columns={columns}
        rows={rows}
        getRowId={(row) => String(rows.indexOf(row))}
        maxHeight="220px"
      />
    </div>
  );
}
