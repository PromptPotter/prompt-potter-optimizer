import { memo } from "react";
import { Bar, Line } from "react-chartjs-2";
import type { Evidence, SubjectReading, WinnerChainPoint } from "@/lib/api/types";
import { maskedSubject } from "@/lib/api/reads";
import {
  DataTable,
  Empty,
  IntervalRow,
  intervalDomain,
  type Column,
  type IntervalDomain,
} from "@/components/ui";
import { cx } from "@/lib/cx";
import { fmtMetric, fmtMetricInterval, type MetricFormat } from "@/lib/format";
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
      <Empty>No selected subject reached a cell under this metric, so there is nothing to plot.</Empty>
    );
  }

  const datasets = series.map((s, i) => ({
    label: s.label,
    data: cells.map((cell) => s.values[cell] ?? null),
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
  const columns: Column<SubjectReading>[] = [
    {
      id: "subject",
      label: "subject",
      width: "minmax(10ch, 18ch)",
      cell: (s) => (
        <span className="cmp-coverage-name" title={s.key}>
          {s.label}
        </span>
      ),
    },
    ...cells.map(
      (cell): Column<SubjectReading> => ({
        id: cell,
        label: "",
        width: "1.1rem",
        cell: (s) => {
          const scored = s.values[cell] !== undefined;
          const mark = scored ? "" : s.unscorable_cells.includes(cell) ? "x" : "?";
          return (
            <span
              className={cx("cmp-coverage-cell", scored && "is-scored")}
              title={`${cell}: ${
                scored ? "scored" : mark === "x" ? "measured, unreadable here" : "not measured"
              }`}
            >
              {mark}
            </span>
          );
        },
      }),
    ),
  ];
  return (
    <div className="cmp-coverage">
      <p className="l4-subtle">
        Cell coverage — <code>?</code> not measured by this subject, <code>x</code> measured but
        unreadable under this metric. Never a zero.
      </p>
      <DataTable
        ariaLabel="Cell coverage"
        className="cmp-coverage-grid"
        columns={columns}
        rows={evidence.subjects}
        getRowId={(s) => s.key}
      />
    </div>
  );
}

function Merged({ evidence }: { evidence: Evidence }) {
  const rows = evidence.subjects;
  const domain = intervalDomain(
    [...rows, ...rows.flatMap((r) => r.winner_chain ?? [])].flatMap((r) =>
      r.value === null ? [] : [r.ci_lo ?? r.value, r.value, r.ci_hi ?? r.value],
    ),
  );
  if (domain === null) {
    return (
      <Empty>No subject in this selection could be read under this metric.</Empty>
    );
  }
  return (
    <div className="ov-forest">
      {rows.map((r, i) => (
        <div key={r.key}>
          <MergedRow
            label={r.label}
            title={r.key}
            row={r}
            metric={evidence.metric.spec}
            domain={domain}
            ink={seriesVar(i)}
            className={cx(r.comparable === false && "cmp-row-muted")}
          />
          {r.winner_chain?.map((p: WinnerChainPoint) => (
            <MergedRow
              key={`${r.key}|${p.round}|${p.candidate_id}`}
              label={`r${p.round} ${p.label}`}
              title={p.candidate_id}
              row={p}
              metric={evidence.metric.spec}
              domain={domain}
              ink={seriesVar(i)}
              className="cmp-row-nested"
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
  metric,
  domain,
  ink,
  className,
}: {
  label: string;
  title: string;
  row: { value: number | null; ci_lo: number | null; ci_hi: number | null; n_cells: number };
  metric: MetricFormat;
  domain: IntervalDomain;
  ink: string;
  className: string;
}) {
  const value = fmtMetric(metric, row.value);
  const interval = fmtMetricInterval(metric, row.ci_lo, row.ci_hi);
  const cells = `${row.n_cells} cell${row.n_cells === 1 ? "" : "s"}`;
  return (
    <IntervalRow
      className={className}
      label={label}
      title={title}
      value={
        <>
          {value}
          <span className="l4-subtle">
            {" "}
            {interval} · {cells}
          </span>
        </>
      }
      domain={domain}
      point={row.value}
      lo={row.ci_lo}
      hi={row.ci_hi}
      pointInk={ink}
      intervalInk="var(--color-ci)"
      ariaLabel={`${label}: ${value} ${interval} over ${cells}`}
    />
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
            <code title={s.key}>{s.label}</code>
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
                mask
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
