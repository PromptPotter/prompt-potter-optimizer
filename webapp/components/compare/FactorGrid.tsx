"use client";
// The factorial read, two factors crossed at a time. Cells and margins are served and pooled server-side
// (`webapp/CLAUDE.md` § Scoring authority); fixing a third factor belongs in the selection, never a client filter.

import { useMemo, useState } from "react";
import { CardFrame } from "@/components/ui";
import type { Evidence, FactorReading } from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { fmtMetricInterval, fmtMetricValue } from "@/lib/format";

function clip(value: string, width: number): string {
  return value.length <= width ? value : `${value.slice(0, width - 1)}…`;
}

// JSON, not a delimiter: a level is arbitrary operator text, so no separator is safe.
function cellKey(row: string, col: string): string {
  return JSON.stringify([row, col]);
}

function FactorPicker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: FactorReading[];
  onChange: (key: string) => void;
}) {
  return (
    <label className="l4-factor-pick">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((f) => (
          <option key={f.key} value={f.key}>
            {f.key}
            {f.confounded_with.length > 0 ? " (aliased)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}

export function FactorGrid({
  evidence,
  grid,
  onGrid,
}: {
  evidence: Evidence;
  grid: string;
  onGrid: (grid: string) => void;
}) {
  const factors = evidence.factors;
  // Separable first: an aliased factor on an axis draws another factor's grid under the wrong heading.
  const ordered = useMemo(
    () => [...factors].sort((a, b) => a.confounded_with.length - b.confounded_with.length),
    [factors],
  );
  const [rowKey, setRowKey] = useState<string>(() => ordered[0]?.key ?? "");
  const [colKey, setColKey] = useState<string>(() => ordered[1]?.key ?? "");

  const served = evidence.grid;
  const unit = evidence.metric.spec.unit;
  const cross = (row: string, col: string) => {
    setRowKey(row);
    setColKey(col);
    if (grid && row && col && row !== col) onGrid(`${row},${col}`);
  };

  if (factors.length === 0) {
    return (
      <CardFrame title="Factor grid">
        <p className="l4-empty">
          Nothing varies across these subjects — they ran the same way on the same dataset, which
          makes them replicates rather than a grid. The spread between them is the noise reading.
        </p>
      </CardFrame>
    );
  }

  const sameAxis = rowKey === colKey;
  const row = factors.find((f) => f.key === (served?.row_key ?? rowKey));
  const col = factors.find((f) => f.key === (served?.col_key ?? colKey));
  const at = new Map((served?.cells ?? []).map((c) => [cellKey(c.row, c.col), c]));

  return (
    <CardFrame title="Factor grid">
      <p className="l4-lede">
        {factors.length} factor(s) vary across {evidence.subjects.length} subject(s), read on{" "}
        {evidence.metric.spec.axis_label}. Cross two to see which COMBINATION leads; the marginals
        above answer which level leads on average.
      </p>

      <div className="l4-factor-controls">
        <FactorPicker
          label="rows"
          value={rowKey}
          options={ordered}
          onChange={(k) => cross(k, colKey)}
        />
        <FactorPicker
          label="columns"
          value={colKey}
          options={ordered}
          onChange={(k) => cross(rowKey, k)}
        />
        <button
          type="button"
          className="cmp-button"
          disabled={sameAxis || !rowKey || !colKey}
          onClick={() => onGrid(`${rowKey},${colKey}`)}
        >
          Cross them
        </button>
      </div>

      {sameAxis && (
        <p className="l4-warn">
          The same factor is on both axes, so only the diagonal could hold a subject. Pick a second
          one.
        </p>
      )}

      {(row?.confounded_with.length ?? 0) > 0 || (col?.confounded_with.length ?? 0) > 0 ? (
        <p className="l4-warn">
          {[row, col]
            .filter((f) => f && f.confounded_with.length > 0)
            .map((f) => `${f?.key} is aliased by ${f?.confounded_with.join(", ")}`)
            .join("; ")}
          . Those factors cut this selection into the identical groups, so no evidence here can tell
          them apart — this grid shows one contrast under several names.
        </p>
      ) : null}

      {!served ? (
        <p className="l4-note">
          No pair crossed yet. The cells are pooled on the server, so this is a read of its own
          rather than a regrouping of what is already on screen.
        </p>
      ) : (
        <>
          {served.note && <p className="l4-warn">{served.note}</p>}
          <div className="l4-table-wrap">
            <table className="l4-table l4-factor-grid">
              <thead>
                <tr>
                  <th scope="col">
                    {served.row_key} \ {served.col_key}
                  </th>
                  {(col?.levels ?? []).map((lv) => (
                    <th key={lv.level} scope="col" title={lv.level}>
                      {clip(lv.level, 22)}
                    </th>
                  ))}
                  <th scope="col" className="l4-margin">
                    row marginal
                  </th>
                </tr>
              </thead>
              <tbody>
                {(row?.levels ?? []).map((rlv) => (
                  <tr key={rlv.level}>
                    <th scope="row" title={rlv.level}>
                      {clip(rlv.level, 22)}
                    </th>
                    {(col?.levels ?? []).map((clv) => {
                      const cell = at.get(cellKey(rlv.level, clv.level));
                      return (
                        <td key={clv.level} className={cx(!cell && "l4-cell-empty")}>
                          {!cell ? (
                            <span title="No subject in this selection ran that combination.">—</span>
                          ) : (
                            <span title={fmtMetricInterval(unit, cell.ci_lo, cell.ci_hi)}>
                              {fmtMetricValue(unit, cell.value)}
                              <span className="l4-cell-n">
                                {" "}
                                · {cell.n_cells} cells, {cell.subjects.length} subj
                              </span>
                            </span>
                          )}
                        </td>
                      );
                    })}
                    <td className="l4-margin" title={fmtMetricInterval(unit, rlv.ci_lo, rlv.ci_hi)}>
                      {fmtMetricValue(unit, rlv.value)}
                    </td>
                  </tr>
                ))}
                <tr>
                  <th scope="row" className="l4-margin">
                    column marginal
                  </th>
                  {(col?.levels ?? []).map((clv) => (
                    <td
                      key={clv.level}
                      className="l4-margin"
                      title={fmtMetricInterval(unit, clv.ci_lo, clv.ci_hi)}
                    >
                      {fmtMetricValue(unit, clv.value)}
                    </td>
                  ))}
                  <td />
                </tr>
              </tbody>
            </table>
          </div>

          {served.marginalised.length > 0 && (
            <p className="l4-note">
              Marginalised into every cell above: {served.marginalised.join(", ")}. To read one held
              fixed, narrow the SELECTION to the subjects at that level — the server then pools over
              what you are actually looking at, which a filter here could not.
            </p>
          )}
        </>
      )}
    </CardFrame>
  );
}
