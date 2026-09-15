"use client";

import type { DraftCampaignWire } from "@/lib/api";

// The data being configured, shown where it is configured. A check-in has no
// `datasets/{slug}/` yet — that is written at Start — so `/datasets/{name}/preview` cannot
// answer for it and the hard-samples hero above legitimately has nothing to plot. These
// rows are on the draft the panel already holds, keyed by the RAW upload headers, so they
// render before any column is mapped: the operator picks the mapping by reading the data,
// not the other way round.
//
// Read-only by construction. `ColumnMappingPicker` is the one control over those two
// columns; this marks what that picker chose and offers no second way to change it.
export function DatasetPreview({ draft }: { draft: DraftCampaignWire }) {
  const { sample_preview: rows, headers } = draft;
  if (rows.length === 0 || headers.length === 0) return null;

  // A column must be NAMED to carry a role. `column_query` is `""` until the operator confirms
  // the mapping, so an unnamed CSV header would otherwise match it and paint itself the chosen
  // input on the very surface the operator is reading in order to choose.
  const roleOf = (h: string) =>
    !h ? null : h === draft.column_query ? "input" : h === draft.column_ground_truth ? "target" : null;

  return (
    <section className="setup-preview">
      <header className="setup-preview-head">
        <span className="setup-preview-title">Your data</span>
        <span className="setup-preview-sub">
          {draft.n_samples.toLocaleString()} row{draft.n_samples === 1 ? "" : "s"} ·{" "}
          {headers.length} column{headers.length === 1 ? "" : "s"} · first {rows.length} shown
        </span>
      </header>
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
                  // NOT `?? ""`. A ragged CSV row parses without its trailing keys, and a blank
                  // cell rendered for a MISSING one reads identically to a genuinely empty value
                  // — here, where the operator judges the data well enough to map its columns.
                  // The dash is a mark of absence, not a stand-in value.
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
