"use client";

import { useState } from "react";
import { CardFrame, DataTable, Empty, type Column } from "@/components/ui";
import type { Evidence, FactorReading } from "@/lib/api/types";
import { fmtInterval, fmtUnit } from "@/lib/format";

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
  // Served separable first (`application/evidence/grid.py::factors`), so the first two open the grid.
  const [rowKey, setRowKey] = useState<string>(() => factors[0]?.key ?? "");
  const [colKey, setColKey] = useState<string>(() => factors[1]?.key ?? "");

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
        <Empty>
          Nothing varies across these subjects — they ran the same way on the same dataset, which
          makes them replicates rather than a grid. The spread between them is the noise reading.
        </Empty>
      </CardFrame>
    );
  }

  const sameAxis = rowKey === colKey;
  const row = factors.find((f) => f.key === (served?.row_key ?? rowKey));
  const col = factors.find((f) => f.key === (served?.col_key ?? colKey));
  const at = new Map((served?.cells ?? []).map((c) => [cellKey(c.row, c.col), c]));

  return (
    <CardFrame title="Factor grid">
      <p className="note-lede">
        {factors.length} factor(s) vary across {evidence.subjects.length} subject(s), read on{" "}
        {evidence.metric.spec.axis_label}. Cross two to see which COMBINATION leads; the marginals
        above answer which level leads on average.
      </p>

      <div className="l4-factor-controls">
        <FactorPicker
          label="rows"
          value={rowKey}
          options={factors}
          onChange={(k) => cross(k, colKey)}
        />
        <FactorPicker
          label="columns"
          value={colKey}
          options={factors}
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
        <p className="note-warn">
          The same factor is on both axes, so only the diagonal could hold a subject. Pick a second
          one.
        </p>
      )}

      {(row?.confounded_with.length ?? 0) > 0 || (col?.confounded_with.length ?? 0) > 0 ? (
        <p className="note-warn">
          {[row, col]
            .filter((f) => f && f.confounded_with.length > 0)
            .map((f) => `${f?.key} is aliased by ${f?.confounded_with.join(", ")}`)
            .join("; ")}
          . Those factors cut this selection into the identical groups, so no evidence here can tell
          them apart — this grid shows one contrast under several names.
        </p>
      ) : null}

      {!served ? (
        <p className="note-info">
          No pair crossed yet. The cells are pooled on the server, so this is a read of its own
          rather than a regrouping of what is already on screen.
        </p>
      ) : (
        <>
          {served.note && <p className="note-warn">{served.note}</p>}
          <DataTable
            ariaLabel={`${served.row_key} by ${served.col_key}`}
            className="l4-factor-grid"
            columns={gridColumns(served.row_key, served.col_key, col?.levels ?? [], at, unit)}
            rows={[
              ...(row?.levels ?? []).map((level): GridRow => ({ kind: "level", level })),
              { kind: "margin" },
            ]}
            getRowId={(r) => (r.kind === "margin" ? "margin" : `level:${r.level.level}`)}
          />

          {served.marginalised.length > 0 && (
            <p className="note-info">
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

type Level = FactorReading["levels"][number];
type Cell = NonNullable<Evidence["grid"]>["cells"][number];
type Unit = Evidence["metric"]["spec"]["unit"];
// The last row is the column marginals.
type GridRow = { kind: "level"; level: Level } | { kind: "margin" };

function gridColumns(
  rowKey: string,
  colKey: string,
  colLevels: readonly Level[],
  at: ReadonlyMap<string, Cell>,
  unit: Unit,
): Column<GridRow>[] {
  const margin = (lv: Level) => (
    <span className="l4-margin" title={fmtInterval(unit, lv.ci_lo, lv.ci_hi)}>
      {fmtUnit(unit, lv.value)}
    </span>
  );
  return [
    {
      id: "row",
      label: `${rowKey} \\ ${colKey}`,
      width: "minmax(14ch, 1fr)",
      cell: (r) =>
        r.kind === "margin" ? (
          <span className="l4-margin">column marginal</span>
        ) : (
          <span className="l4-factor-level" title={r.level.level}>
            {clip(r.level.level, 22)}
          </span>
        ),
    },
    ...colLevels.map(
      (clv, i): Column<GridRow> => ({
        id: `col:${i}`,
        label: <span title={clv.level}>{clip(clv.level, 22)}</span>,
        width: "minmax(16ch, 1fr)",
        cell: (r) => {
          if (r.kind === "margin") return margin(clv);
          const cell = at.get(cellKey(r.level.level, clv.level));
          return !cell ? (
            <span className="l4-cell-empty" title="No subject in this selection ran that combination.">
              —
            </span>
          ) : (
            <span title={fmtInterval(unit, cell.ci_lo, cell.ci_hi)}>
              {fmtUnit(unit, cell.value)}
              <span className="l4-cell-n">
                {" "}
                · {cell.n_cells} cells, {cell.subjects.length} subj
              </span>
            </span>
          );
        },
      }),
    ),
    {
      id: "row-margin",
      label: "row marginal",
      width: "12ch",
      align: "end",
      cell: (r) => (r.kind === "margin" ? null : margin(r.level)),
    },
  ];
}
