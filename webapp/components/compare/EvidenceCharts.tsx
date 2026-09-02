"use client";
// The visual forms of one served comparison. Four ways to look at the same numbers, because which
// one reads depends on the question: bars beside each other for "who is higher on this cell", bars
// on one axis for "how do the profiles overlap", lines for "does the shape track across cells",
// and MERGED for "forget the cells — what is each subject worth, and how sure are we", which is
// the one the intervals belong on.
//
// EVERY value plotted comes off `Evidence.subjects` as served — the per-cell map and the merged
// estimate are fields of ONE row, so a bar and its interval cannot come from two lists that
// disagree. The only work here is layout and colour; nothing recomputes a level, a mean, a bound
// or a verdict (`webapp/CLAUDE.md` § Scoring authority).
//
// The axis is `metric.covered_cells`: every cell ANY subject reached. Not the intersection — a
// subject that came up short would simply not be on the board there, and "who failed to answer
// this cell" is exactly what the operator is looking for. What a blank means is served per cell
// and rendered as two different glyphs by `Coverage` below; the paired tests and the variance
// split stay on `scored_cells`, which is a different question and a different denominator.

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

// What to call one channel. The label is served (`SubjectReading.label`) — a campaign by its own
// name, a branch by its cycle, a searchpoint by its minted label — so nothing here decides it.
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
  // Subscribe to theme so a flip re-runs this component and the `getCss` palette below resolves
  // afresh. Built inline rather than memoized, same as the other charts: this reads a one-shot
  // fetch that changes only when the SELECTION does, not the 2 s poll, so there is no per-tick
  // cost to guard against — and a memo keyed on the theme version is a dependency the linter
  // cannot verify and a reader cannot trust.
  useThemeVersion();
  const cells = evidence.metric.covered_cells;
  const series = evidence.subjects;
  const axis = evidence.metric.spec.axis_label;

  // Merged answers BEFORE the empty guard: it needs no shared cell, so a selection with nothing
  // in common still reports what each subject is worth on its own.
  if (view === "merged") {
    return <Merged evidence={evidence} />;
  }

  if (cells.length === 0) {
    return (
      <p className="l4-empty">
        No selected subject reached a cell under this metric, so there is nothing to plot.
      </p>
    );
  }

  const datasets = series.map((s, i) => ({
    label: seriesLabel(s),
    // A cell absent from this subject cannot be plotted as 0 — that reads as a measured
    // floor. `null` leaves a gap, which is what it is; the strip below says WHICH absence.
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
          // `stacked` on BOTH axes is what puts one bar per cell with the subjects sharing it;
          // false on both is the grouped form, one bar per subject per cell.
          scales: axisScales(axis, view === "overlaid"),
        })}
      />
    </div>
  );
});

// Why a bar is missing, per cell — the half a gap in the chart cannot carry. Two absences, two
// facts: `?` the subject never measured this cell, `x` it measured it and this metric cannot read
// the row. Both are SERVED (`values` and `unscorable_cells`); nothing here infers which is which,
// which is the whole reason the server names the unscorable cells rather than counting them.
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

// One row per subject — the cells merged into a single estimate with its served 95% interval.
// The axis spans the whole selection's [min lo, max hi] rather than the point estimates', or a
// whisker would run off the end of the row it belongs to.
//
// Drawn as the same `ov-axis` SVG row the outer-signal forest uses, so the one visual idiom this
// app has for "an estimate and how sure we are" has one rendering. A subject with no interval
// shows a dot alone: below two scored cells there is no spread, and a zero-width whisker would
// read as a perfect measurement. A subject with no VALUE keeps its row with an em-dash —
// vanishing from the chart is fabrication by omission.
const AXIS_W = 220;
const ROW_H = 18;

// The whole selection's bounds, so every row — and every winner-chain point under it — is drawn on
// ONE scale. Read off the trajectories too: a chain point outside the heads' range would be
// clipped to the edge and read as a value it never had.
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
      <p className="l4-empty">No subject in this selection could be read under this metric.</p>
    );
  }
  return (
    // `cmp-forest` only turns OFF the trend emphasis the shared row style carries: in the outer
    // signal panel the rows are rounds and the last one is the latest, so bolding it is the
    // point; here they are subjects in run order and bolding the newest asserts a winner.
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
          {/* The branch behind this channel, origin first — the same estimate-and-interval row,
              indented, so a head and the points it came from read on one scale. */}
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
  // Each row is merged over THIS subject's own cells, not the shared axis the other three views
  // plot — so the count belongs on the row. Without it a wide interval and a short one look like
  // a difference in the subjects rather than in how many cells each was read over.
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

// The channel roster, and where a scoring mask is opened on one. A subject the server marked not
// comparable to the rest carries the tag here — served verdict, never a client guess at which row
// is the odd one out — because that is the one thing a coloured swatch beside a number cannot say.
export function SeriesLegend({
  evidence,
  masking,
  onMask,
}: {
  evidence: Evidence;
  // The BARE address whose editor is open, or null. Bare rather than the key, so applying a mask
  // does not close the form that applied it.
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
            {/* Only a course has elections to re-decide, which is the server's own rule; a
                sample subset alone would be offered on every kind, and one control that means
                two things depending on the row is worse than the narrower one. */}
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
