"use client";

import type { DraftCampaignWire } from "@/lib/api";
import { Toolbar, ToolbarSpacer } from "@/components/ui";

// The draft's sample rows, keyed by RAW upload headers — a check-in has no `datasets/{slug}/`
// until Start, so `/cells` cannot answer. Read-only: `ColumnMappingPicker` owns the mapping.
export function DatasetPreview({ draft }: { draft: DraftCampaignWire }) {
  const { sample_preview: rows, headers } = draft;
  if (rows.length === 0 || headers.length === 0) return null;

  // `column_query` is `""` until confirmed, so an unnamed header must not match it.
  const roleOf = (h: string) =>
    !h ? null : h === draft.column_query ? "input" : h === draft.column_ground_truth ? "target" : null;

  return (
    <section className="setup-preview">
      <Toolbar>
        <span className="setup-preview-title">Your data</span>
        <ToolbarSpacer />
        <span className="setup-preview-sub">
          {draft.n_samples.toLocaleString()} row{draft.n_samples === 1 ? "" : "s"} ·{" "}
          {headers.length} column{headers.length === 1 ? "" : "s"} · first {rows.length} shown
        </span>
      </Toolbar>
      <div className="ds-preview-scroll">
        <table className="ds-preview-table">
          <thead>
            <tr>
              {headers.map((h) => {
                const role = roleOf(h);
                return (
                  <th key={h} className={role ? `is-${role}` : undefined} scope="col">
                    {h}
                    {role ? <span className="ds-preview-role">{role}</span> : null}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {headers.map((h) => {
                  const role = roleOf(h);
                  // NOT `?? ""`: a ragged row lacks trailing keys, and a missing cell must not
                  // read as an empty value.
                  const v = row[h];
                  return (
                    <td
                      key={h}
                      className={role ? `is-${role}` : undefined}
                      title={v ?? "This row carries no value for this column."}
                    >
                      {v ?? "—"}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
