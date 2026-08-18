"use client";
// The visual forms of one served comparison. Four ways to look at the same numbers, because
// which one reads depends on the question: bars beside each other for "who is higher on this
// cell", bars on one axis for "how do the profiles overlap", lines for "does the shape track
// across cells", forest for "does any interval clear the others".
//
// EVERY value plotted comes off `Evidence.cells` / `Evidence.shared_cells` as served. The only
// work here is layout and colour, which are presentation — nothing recomputes a level, a mean or
// a verdict (`webapp/CLAUDE.md` § Scoring authority).
//
// The axis is `shared_cells`: the cells EVERY selected campaign measured. A campaign scored on a
// cell the others missed would otherwise carry that cell's difficulty into a visual comparison —
// the same restriction the server applies before decomposing variance.

import { memo } from "react";
import { Bar, Line } from "react-chartjs-2";
import type { Evidence } from "@/lib/api";
import { barChartDefaults, ensureChartRegistered, getCss, lineChartDefaults, useThemeVersion } from "@/lib/theme";

ensureChartRegistered();

export type CompareView = "grouped" | "overlaid" | "lines" | "forest";

// Cycled per selected campaign. Defined as :root custom properties in
// `app/styles/domains/compare.css` so canvas and DOM read one palette and a theme flip moves
// both — `getCss` cannot resolve a `var()` inside a canvas paint.
const SERIES_SLOTS = 8;

export function seriesColor(index: number): string {
  return getCss(`--cmp-series-${(index % SERIES_SLOTS) + 1}`);
}

function shortId(campaignId: string): string {
  // The `{dataset}__{rand6}` suffix is what distinguishes two runs of one origin.
  const cut = campaignId.lastIndexOf("__");
  return cut === -1 ? campaignId : campaignId.slice(cut + 2);
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
  const cells = evidence.shared_cells;
  const series = evidence.cells;

  const datasets = series.map((s, i) => ({
    label: shortId(s.campaign_id),
    // A cell absent from this campaign cannot be plotted as 0 — that reads as a measured
    // floor. `null` leaves a gap, which is what it is.
    data: cells.map((cell) => {
      const level = s.levels[cell];
      return level === undefined ? null : level;
    }),
    borderColor: seriesColor(i),
    backgroundColor: seriesColor(i),
    borderWidth: view === "lines" ? 1.5 : 0,
    pointRadius: view === "lines" ? 2.5 : 0,
    tension: 0.25,
  }));

  if (cells.length === 0 || series.length === 0) {
    return (
      <p className="l4-empty">
        No cell every selected campaign measured — nothing can be plotted side by side. Campaigns
        on different datasets never share one.
      </p>
    );
  }

  const labels = cells.map((c) => c.split("/").pop() ?? c);

  if (view === "forest") {
    return <Forest evidence={evidence} />;
  }

  if (view === "lines") {
    return (
      <div className="cmp-canvas">
        <Line
          data={{ labels, datasets }}
          options={lineChartDefaults({
            plugins: { legend: { display: false } },
            scales: { x: { ticks: { color: getCss("--color-text-tertiary") } }, y: { ticks: { color: getCss("--color-text-tertiary") } } },
          })}
        />
      </div>
    );
  }

  return (
    <div className="cmp-canvas">
      <Bar
        data={{ labels, datasets }}
        options={barChartDefaults({
          plugins: { legend: { display: false } },
          scales: {
            // `stacked` on BOTH axes is what puts one bar per cell with the campaigns sharing
            // it; false on both is the grouped form, one bar per campaign per cell.
            x: { stacked: view === "overlaid", ticks: { color: getCss("--color-text-tertiary") } },
            y: { stacked: view === "overlaid", ticks: { color: getCss("--color-text-tertiary") } },
          },
        })}
      />
    </div>
  );
});

// One row per campaign: its served mean level against the selection's own range. No interval is
// drawn per campaign because none is served per campaign — the interval that exists is the
// selection's paired SE, and pinning it to one row would claim a precision that row never had.
function Forest({ evidence }: { evidence: Evidence }) {
  const rows = evidence.origins.filter((o) => o.origin_level !== null);
  const levels = rows.map((o) => o.origin_level as number);
  if (levels.length === 0) {
    return <p className="l4-empty">No campaign in this selection priced a cell.</p>;
  }
  const lo = Math.min(...levels);
  const hi = Math.max(...levels);
  const span = hi - lo || 1;
  return (
    <div className="ov-forest">
      {rows.map((o, i) => (
        <div className="ov-row" key={o.campaign_id}>
          <span className="ov-cell-label" title={o.campaign_id}>
            {shortId(o.campaign_id)}
          </span>
          <span className="cmp-forest-track">
            <span
              className="cmp-forest-dot"
              style={{
                left: `${(((o.origin_level as number) - lo) / span) * 100}%`,
                background: seriesColor(i),
              }}
            />
          </span>
          <span className="ov-cell-val">{(o.origin_level as number).toFixed(3)}</span>
        </div>
      ))}
    </div>
  );
}

export function SeriesLegend({ evidence }: { evidence: Evidence }) {
  return (
    <ul className="cmp-legend">
      {evidence.cells.map((s, i) => (
        <li key={s.campaign_id}>
          <span className="cmp-swatch" style={{ background: seriesColor(i) }} aria-hidden="true" />
          <code>{s.campaign_id}</code>
        </li>
      ))}
    </ul>
  );
}
