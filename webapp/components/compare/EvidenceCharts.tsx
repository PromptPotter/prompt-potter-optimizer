"use client";

import { memo } from "react";
import { Bar, Line } from "react-chartjs-2";
import type { Evidence, SubjectReading, WinnerChainPoint } from "@/lib/api";
import { maskedSubject } from "@/lib/api/reads";
import { cx } from "@/lib/cx";
import { fmtMetricInterval, fmtMetricValue, shortId } from "@/lib/format";
import type { MetricUnit } from "@/lib/format";
import { barChartDefaults, ensureChartRegistered, getCss, lineChartDefaults, seriesColor, seriesVar, useThemeVersion } from "@/lib/theme";

ensureChartRegistered();

export type CompareView = "grouped" | "overlaid" | "lines" | "merged";

function axisScales(title: string, stacked: boolean) {
  const tick = { color: getCss("--color-text-secondary") };
  return {
    x: { stacked, ticks: tick },
    y: {
      stacked,
      ticks: tick,
      title: {
        display: true,
        text: title,
        align: "end" as const,
        color: getCss("--color-text-secondary"),
        font: { size: 11 },
      },
    },
  };
}

function seriesLabel(row: SubjectReading): string {
  return row.kind === "campaign" ? shortId(row.label) : row.label;
}

export const EvidenceCharts = memo(function EvidenceCharts({
  evidence,
  view,
}: {
  evidence: Evidence;
  view: CompareView;
}) {
  useThemeVersion();
  const cells = evidence.metric.covered_cells;
  const series = evidence.subjects;
  const axis = evidence.metric.spec.axis_label;

  // Merged before the empty guard: it needs no shared cell.
  if (view === "merged") {
    return <Merged evidence={evidence} />;
  }

  if (cells.length === 0) {
    return (
      <p className="note-empty">
        No selected subject reached a cell under this metric, so there is nothing to plot.
      </p>
    );
  }

  const datasets = series.map((s, i) => ({
    label: seriesLabel(s),
    data: cells.map((cell) => {
      const value = s.values[cell];
      return value === undefined ? null : value;
    }),
    borderColor: seriesColor(i),
    backgroundColor: seriesColor(i),
    borderWidth: view === "lines" ? 1.5 : 0,
    pointRadius: view === "lines" ? 2.5 : 0,
    tension: 0.25,
  }));

  const labels = cells.map((c) => c.split("/").pop() ?? c);

  if (view === "lines") {
    return (
      <div className="cmp-canvas">
        <Line
          aria-label={`${axis} across ${cells.length} cells, one line per subject`}
          data={{ labels, datasets }}
          options={lineChartDefaults({
            plugins: { legend: { display: false } },
            scales: axisScales(axis, false),
          })}
        />
      </div>
    );
  }

  return (
    <div className="cmp-canvas">
      <Bar
        aria-label={`${axis} across ${cells.length} cells, one bar per subject`}
        data={{ labels, datasets }}
        options={barChartDefaults({
          plugins: { legend: { display: false } },
          scales: axisScales(axis, view === "overlaid"),
        })}
      />
    </div>
  );
});

export function Coverage({ evidence }: { evidence: Evidence }) {
  const cells = evidence.metric.covered_cells;
  const gaps = evidence.subjects.some(
    (s) => s.unscorable_cells.length > 0 || s.n_cells < cells.length,
  );
  if (!gaps || cells.length === 0) return null;
  return (
    <div className="cmp-coverage">
      <table className="cmp-coverage-grid">
        <caption className="l4-subtle">
          Cell coverage — <code>?</code> not measured by this subject, <code>x</code> measured but
          unreadable under this metric. Never a zero.
        </caption>
        <tbody>
          {evidence.subjects.map((s) => {
            const unscorable = new Set(s.unscorable_cells);
            return (
              <tr key={s.key}>
                <th scope="row" className="cmp-coverage-name" title={s.key}>
                  {seriesLabel(s)}
                </th>
                {cells.map((cell) => {
                  const scored = s.values[cell] !== undefined;
                  const mark = scored ? "" : unscorable.has(cell) ? "x" : "?";
                  return (
                    <td
                      key={cell}
                      className={cx("cmp-coverage-cell", scored && "is-scored")}
                      title={`${cell}: ${
                        scored ? "scored" : mark === "x" ? "measured, unreadable here" : "not measured"
                      }`}
                    >
                      {mark}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const AXIS_W = 220;
const ROW_H = 18;

function scaleOver(rows: readonly { value: number | null; ci_lo: number | null; ci_hi: number | null }[]) {
  const bounds = rows.flatMap((r) =>
    r.value === null ? [] : [r.ci_lo ?? r.value, r.value, r.ci_hi ?? r.value],
  );
  if (bounds.length === 0) return null;
  const lo = Math.min(...bounds);
  const hi = Math.max(...bounds);
  const pad = (hi - lo || 1) * 0.1;
  const [d0, d1] = [lo - pad, hi + pad];
  return (v: number) => 4 + ((v - d0) / (d1 - d0)) * (AXIS_W - 8);
}

function Merged({ evidence }: { evidence: Evidence }) {
  const rows = evidence.subjects;
  const x = scaleOver([...rows, ...rows.flatMap((r) => r.winner_chain ?? [])]);
  if (x === null) {
    return (
      <p className="note-empty">No subject in this selection could be read under this metric.</p>
    );
  }
  return (
    // `cmp-forest` turns off `ov-forest`'s newest-row emphasis: these rows are subjects, not rounds.
    <div className="ov-forest cmp-forest">
      {rows.map((r, i) => (
        <div key={r.key}>
          <MergedRow
            label={seriesLabel(r)}
            title={r.key}
            row={r}
            unit={evidence.metric.spec.unit}
            x={x}
            colour={seriesVar(i)}
            muted={r.comparable === false}
          />
          {r.winner_chain?.map((p: WinnerChainPoint) => (
            <MergedRow
              key={`${r.key}|${p.round}|${p.candidate_id}`}
              label={`r${p.round} ${p.label}`}
              title={p.candidate_id}
              row={p}
              unit={evidence.metric.spec.unit}
              x={x}
              colour={seriesVar(i)}
              nested
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function MergedRow({
  label,
  title,
  row,
  unit,
  x,
  colour,
  muted,
  nested,
}: {
  label: string;
  title: string;
  row: { value: number | null; ci_lo: number | null; ci_hi: number | null; n_cells: number };
  unit: MetricUnit;
  x: (v: number) => number;
  colour: string;
  muted?: boolean;
  nested?: boolean;
}) {
  const value = fmtMetricValue(unit, row.value);
  const interval = fmtMetricInterval(unit, row.ci_lo, row.ci_hi);
  const cells = `${row.n_cells} cell${row.n_cells === 1 ? "" : "s"}`;
  return (
    <div className={cx("ov-row", nested && "cmp-row-nested", muted && "cmp-row-muted")}>
      <span className="ov-cell-label" title={title}>
        {label}
      </span>
      <svg
        className="ov-axis"
        width={AXIS_W}
        height={ROW_H}
        viewBox={`0 0 ${AXIS_W} ${ROW_H}`}
        role="img"
        aria-label={`${label}: ${value} ${interval} over ${cells}`}
      >
        {row.ci_lo !== null && row.ci_hi !== null && (
          <line
            x1={x(row.ci_lo)}
            y1={ROW_H / 2}
            x2={x(row.ci_hi)}
            y2={ROW_H / 2}
            stroke="var(--color-ci)"
            strokeWidth={1.5}
          />
        )}
        {row.value !== null && (
          <circle cx={x(row.value)} cy={ROW_H / 2} r={nested ? 2.5 : 3.5} fill={colour} />
        )}
      </svg>
      <span className="ov-cell-val">
        {value}
        <span className="l4-subtle">
          {" "}
          {interval} · {cells}
        </span>
      </span>
    </div>
  );
}

export function SeriesLegend({
  evidence,
  masking,
  onMask,
}: {
  evidence: Evidence;
  masking: string | null;
  onMask: (address: string) => void;
}) {
  return (
    <ul className="cmp-legend">
      {evidence.subjects.map((s, i) => {
        const address = maskedSubject(s, {});
        return (
          <li key={s.key} className={cx(s.comparable === false && "cmp-row-muted")}>
            <span
              className="cmp-swatch"
              style={{ background: seriesVar(i) }}
              aria-hidden="true"
            />
            <code title={s.key}>{seriesLabel(s)}</code>
            <span className="l4-dim">{s.kind}</span>
            {s.mask?.lens && <span className="cmp-tag">masked</span>}
            {s.mask?.samples && (
              <span className="cmp-tag">{s.mask.samples.length} samples</span>
            )}
            {s.comparable === false && <span className="cmp-tag">not comparable</span>}
            {s.comparable === null && <span className="cmp-tag">ruler unknown</span>}
            {s.kind === "course" && (
              <button
                type="button"
                className="cmp-link"
                aria-expanded={masking === address}
                onClick={() => onMask(address)}
              >
                what if…
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
