"use client";
// Capability Matrix — the (target-model × dataset) grid the operator built with
// `matrix measure`. Each cell is classified from its origin accuracy: floor (too
// low), in-band (headroom to optimize), saturated (already acing). Colour AND text
// carry the band (never colour alone); the exact origin % + Wilson CI ride each cell
// so the operator judges reachable headroom. `active_in_panel` cells are ringed —
// those are the blocks the L4 panel measures against. Renders only for the outer
// pp-self loop (DashboardTab's isOuterSelfOpt gate).

import { useFetch } from "@/lib/hooks/useFetch";
import { fetchResourceMatrix, type ResourceCell } from "@/lib/api";
import { CardFrame } from "@/components/ui";
import { cx } from "@/lib/cx";

const BAND_LABEL: Record<string, string> = {
  floor: "floor",
  "in-band": "in-band",
  saturated: "saturated",
  error: "error",
};

function shortModel(m: string): string {
  // Drop the provider-family prefix for the column header; full id in the title.
  return m.includes("/") ? m.slice(m.indexOf("/") + 1) : m;
}

function Cell({ cell }: { cell: ResourceCell | undefined }) {
  if (!cell) return <td className="cap-cell cap-empty">·</td>;
  const acc = cell.origin_accuracy === null ? "—" : `${(cell.origin_accuracy * 100).toFixed(0)}%`;
  const ci =
    cell.origin_accuracy === null
      ? cell.note || "no data"
      : `±${(((cell.wilson_hi - cell.wilson_lo) / 2) * 100).toFixed(0)}`;
  return (
    <td
      className={cx("cap-cell", `cap-band-${cell.band}`, cell.active_in_panel && "cap-active")}
      title={`${cell.dataset} × ${cell.target_model}${cell.provider ? ` (${cell.provider})` : ""} — origin ${acc}, n=${cell.n}, band=${cell.band}${cell.active_in_panel ? " · in panel" : ""}`}
    >
      <span className="cap-acc">{acc}</span>
      <span className="cap-band-tag">{BAND_LABEL[cell.band] ?? cell.band}</span>
      <span className="cap-ci">{ci}</span>
    </td>
  );
}

export function CapabilityMatrixPanel() {
  const { data: matrix } = useFetch((signal) => fetchResourceMatrix(signal), []);
  if (!matrix) return null;

  const cells = matrix.cells;
  const datasets = [...new Set(cells.map((c) => c.dataset))].sort();
  const models = [...new Set(cells.map((c) => c.target_model))].sort();
  const byKey = new Map(cells.map((c) => [`${c.dataset} ${c.target_model}`, c]));

  return (
    <CardFrame
      title="Capability matrix"
      headingTag="h2"
      actions={
        <span className="l4-subtle">
          {cells.length} cell{cells.length === 1 ? "" : "s"}
        </span>
      }
    >
      <p className="l4-lede">
        Which (target-model × dataset) cells carry optimizable signal. Ringed cells are{" "}
        <strong>active in the panel</strong>. Build with{" "}
        <code>promptpotter matrix measure &lt;dataset&gt; --models …</code>.
      </p>
      {cells.length === 0 ? (
        <p className="l4-empty">
          No cells measured yet — run <code>matrix measure</code> to populate the grid.
        </p>
      ) : (
        <div className="l4-table-wrap">
          <table className="cap-table">
            <thead>
              <tr>
                <th scope="col">dataset</th>
                {models.map((m) => (
                  <th key={m} scope="col" title={m}>
                    {shortModel(m)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {datasets.map((ds) => (
                <tr key={ds}>
                  <th scope="row" className="cap-ds">
                    {ds}
                  </th>
                  {models.map((m) => (
                    <Cell key={m} cell={byKey.get(`${ds} ${m}`)} />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CardFrame>
  );
}
