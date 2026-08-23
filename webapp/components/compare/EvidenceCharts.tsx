"use client";
// The visual forms of one served comparison. Four ways to look at the same numbers, because which
// one reads depends on the question: bars beside each other for "who is higher on this cell", bars
// on one axis for "how do the profiles overlap", lines for "does the shape track across cells",
// and MERGED for "forget the cells — what is each campaign worth, and how sure are we", which is
// the one the intervals belong on.
//
// EVERY value plotted comes off `Evidence.campaigns` as served — the per-cell map and the merged
// estimate are fields of ONE row, so a bar and its interval cannot come from two lists that
// disagree. The only work here is layout and colour; nothing recomputes a level, a mean, a bound
// or a verdict (`webapp/CLAUDE.md` § Scoring authority).
//
// The axis is `metric.scored_cells`: the cells EVERY selected campaign SCORED under the chosen
// metric. A campaign scored on a cell the others missed would otherwise carry that cell's
// difficulty into a visual comparison — the same restriction the server applies before
// decomposing variance.

import { memo } from "react";
import { Bar, Line } from "react-chartjs-2";
import type { CampaignReading, Evidence } from "@/lib/api";
import { fmtMetricInterval, fmtMetricValue, shortId } from "@/lib/format";
import type { MetricUnit } from "@/lib/format";
import { barChartDefaults, ensureChartRegistered, getCss, lineChartDefaults, seriesColor, useThemeVersion } from "@/lib/theme";

ensureChartRegistered();

export type CompareView = "grouped" | "overlaid" | "lines" | "merged";

// Both chart forms share an axis pair and differ only by `stacked`. The y axis carries the
// metric's NAME: without it a composed expression plots as bare numbers and nothing on screen
// says what the height is. Shape copied from `candidates/FitnessChart.tsx`, the only other
// titled axis in the app, so the two read alike.
function axisScales(title: string, stacked: boolean) {
  const tick = { color: getCss("--color-text-tertiary") };
  return {
    x: { stacked, ticks: tick },
    y: {
      stacked,
      ticks: tick,
      title: {
        display: true,
        text: title,
        align: "end" as const,
        color: getCss("--color-text-tertiary"),
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
  // Subscribe to theme so a flip re-runs this component and the `getCss` palette below resolves
  // afresh. Built inline rather than memoized, same as the other charts: this reads a one-shot
  // fetch that changes only when the SELECTION does, not the 2 s poll, so there is no per-tick
  // cost to guard against — and a memo keyed on the theme version is a dependency the linter
  // cannot verify and a reader cannot trust.
  useThemeVersion();
  const cells = evidence.metric.scored_cells;
  const series = evidence.campaigns;
  const axis = evidence.metric.spec.axis_label;

  // Merged answers BEFORE the empty guard: it needs no shared cell, so a selection with nothing
  // in common still reports what each campaign is worth on its own.
  if (view === "merged") {
    return <Merged evidence={evidence} />;
  }

  if (cells.length === 0) {
    return (
      <p className="l4-empty">
        No cell was scored by every selected campaign, so nothing can be plotted side by side —
        campaigns on different datasets never share one. Merged still compares them.
      </p>
    );
  }

  const datasets = series.map((s, i) => ({
    label: shortId(s.campaign_id),
    // A cell absent from this campaign cannot be plotted as 0 — that reads as a measured
    // floor. `null` leaves a gap, which is what it is.
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
          aria-label={`${axis} across ${cells.length} cells, one line per campaign`}
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
        aria-label={`${axis} across ${cells.length} cells, one bar per campaign`}
        data={{ labels, datasets }}
        options={barChartDefaults({
          plugins: { legend: { display: false } },
          // `stacked` on BOTH axes is what puts one bar per cell with the campaigns sharing it;
          // false on both is the grouped form, one bar per campaign per cell.
          scales: axisScales(axis, view === "overlaid"),
        })}
      />
    </div>
  );
});

// One row per campaign — the cells merged into a single estimate with its served 95% interval.
// The axis spans the whole selection's [min lo, max hi] rather than the point estimates', or a
// whisker would run off the end of the row it belongs to.
//
// Drawn as the same `ov-axis` SVG row the outer-signal forest uses, so the one visual idiom this
// app has for "an estimate and how sure we are" has one rendering. A campaign with no interval
// shows a dot alone: below two scored cells there is no spread, and a zero-width whisker would
// read as a perfect measurement. A campaign with no VALUE keeps its row with an em-dash —
// vanishing from the chart is fabrication by omission.
const AXIS_W = 220;
const ROW_H = 18;

function Merged({ evidence }: { evidence: Evidence }) {
  const rows = evidence.campaigns;
  const bounds = rows.flatMap((r) =>
    r.value === null ? [] : [r.ci_lo ?? r.value, r.value, r.ci_hi ?? r.value],
  );
  if (bounds.length === 0) {
    return (
      <p className="l4-empty">
        No campaign in this selection could be read under this metric.
      </p>
    );
  }
  const lo = Math.min(...bounds);
  const hi = Math.max(...bounds);
  const pad = (hi - lo || 1) * 0.1;
  const [d0, d1] = [lo - pad, hi + pad];
  const x = (v: number) => 4 + ((v - d0) / (d1 - d0)) * (AXIS_W - 8);
  return (
    // `cmp-forest` only turns OFF the trend emphasis the shared row style carries: in the outer
    // signal panel the rows are rounds and the last one is the latest, so bolding it is the
    // point; here they are campaigns in run order and bolding the newest asserts a winner.
    <div className="ov-forest cmp-forest">
      {rows.map((r, i) => (
        <MergedRow key={r.campaign_id} row={r} unit={evidence.metric.spec.unit} x={x} i={i} />
      ))}
    </div>
  );
}

function MergedRow({
  row,
  unit,
  x,
  i,
}: {
  row: CampaignReading;
  unit: MetricUnit;
  x: (v: number) => number;
  i: number;
}) {
  const value = fmtMetricValue(unit, row.value);
  const interval = fmtMetricInterval(unit, row.ci_lo, row.ci_hi);
  // Each row is merged over THIS campaign's own cells, not the shared axis the other three views
  // plot — so the count belongs on the row. Without it a wide interval and a short one look like
  // a difference in the campaigns rather than in how many cells each was read over.
  const cells = `${row.n_cells} cell${row.n_cells === 1 ? "" : "s"}`;
  const colour = seriesColor(i);
  return (
    <div className="ov-row">
      <span className="ov-cell-label" title={row.campaign_id}>
        {shortId(row.campaign_id)}
      </span>
      <svg
        className="ov-axis"
        width={AXIS_W}
        height={ROW_H}
        viewBox={`0 0 ${AXIS_W} ${ROW_H}`}
        role="img"
        aria-label={`${shortId(row.campaign_id)}: ${value} ${interval} over ${cells}`}
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
          <circle cx={x(row.value)} cy={ROW_H / 2} r={3.5} fill={colour} />
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

export function SeriesLegend({ evidence }: { evidence: Evidence }) {
  return (
    <ul className="cmp-legend">
      {evidence.campaigns.map((s, i) => (
        <li key={s.campaign_id}>
          <span className="cmp-swatch" style={{ background: seriesColor(i) }} aria-hidden="true" />
          <code>{s.campaign_id}</code>
        </li>
      ))}
    </ul>
  );
}
