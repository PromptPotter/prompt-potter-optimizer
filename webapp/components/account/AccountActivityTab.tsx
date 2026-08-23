"use client";
// Activity pane — 3 vertically-stacked time-bucketed charts (spend / requests /
// tokens) over a selectable window, coloured by model or API key.

import { useState } from "react";
import { fmtCompact, fmtUsd } from "@/lib/format";
import { seriesColor, useThemeVersion } from "@/lib/theme";
import { useFetch } from "@/lib/hooks/useFetch";
import {
  fetchActivity,
  type ActivityBucket,
  type ActivityGroupBy,
  type ActivityWindow,
} from "@/lib/api";

const ACTIVITY_WINDOWS: { id: ActivityWindow; label: string }[] = [
  { id: "15m", label: "Past 15 min" },
  { id: "30m", label: "Past 30 min" },
  { id: "1h", label: "Past hour" },
  { id: "3h", label: "Past 3 hours" },
  { id: "1d", label: "Past day" },
  { id: "2d", label: "Past 2 days" },
  { id: "1w", label: "Past week" },
  { id: "1mo", label: "Past month" },
  { id: "1y", label: "Past year" },
];

export function AccountActivityTab() {
  // The SVG paints literal fills, so a theme flip has to re-run this component to re-read them.
  useThemeVersion();
  const [window, setWindow] = useState<ActivityWindow>("1d");
  const [groupBy, setGroupBy] = useState<ActivityGroupBy>("model");
  // useFetch blanks data on the (window, group_by) key change in-render, so the
  // old buckets never render against the new axis labels — the reset is built in.
  const { data, error } = useFetch(() => fetchActivity(window, groupBy), [window, groupBy]);

  const labels = data?.series_labels ?? [];
  const palette = labels.map((_, i) => seriesColor(i));
  return (
    <>
      <div className="activity-window-row">
        <label htmlFor="activity-window-select">Window</label>
        <select
          id="activity-window-select"
          value={window}
          onChange={(e) => setWindow(e.target.value as ActivityWindow)}
        >
          {ACTIVITY_WINDOWS.map((w) => (
            <option key={w.id} value={w.id}>
              {w.label}
            </option>
          ))}
        </select>
        <label htmlFor="activity-group-select">Color by</label>
        <select
          id="activity-group-select"
          value={groupBy}
          onChange={(e) => setGroupBy(e.target.value as ActivityGroupBy)}
        >
          <option value="model">By Model</option>
          <option value="api_key">By API Key</option>
        </select>
      </div>
      {error ? <p className="account-error">{error}</p> : null}
      {labels.length > 0 ? (
        <ul className="activity-legend">
          {labels.map((label, i) => (
            <li key={label}>
              <span
                className="activity-legend-swatch"
                style={{ background: palette[i] }}
                aria-hidden="true"
              />
              <span className="activity-legend-label">{label}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <ActivityBarChart
        title="Spend"
        valueLabel={data ? fmtUsd(data.total_spend_usd) : "$0.00"}
        buckets={data?.buckets ?? []}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_spend}
        totalAccessor={(b) => b.spend_usd}
      />
      <ActivityBarChart
        title="Requests"
        valueLabel={data ? data.total_requests.toLocaleString() : "0"}
        buckets={data?.buckets ?? []}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_requests}
        totalAccessor={(b) => b.requests}
      />
      <ActivityBarChart
        title="Tokens"
        valueLabel={data ? fmtCompact(data.total_tokens) : "0"}
        buckets={data?.buckets ?? []}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_tokens}
        totalAccessor={(b) => b.tokens}
      />
    </>
  );
}

interface ActivityBarChartProps {
  title: string;
  valueLabel: string;
  buckets: ActivityBucket[];
  labels: string[];
  palette: string[];
  accessor: (b: ActivityBucket) => Record<string, number>;
  totalAccessor: (b: ActivityBucket) => number;
}

function ActivityBarChart({
  title,
  valueLabel,
  buckets,
  labels,
  palette,
  accessor,
  totalAccessor,
}: ActivityBarChartProps) {
  const totals = buckets.map(totalAccessor);
  const max = totals.reduce((m, v) => (v > m ? v : m), 0);
  const hasData = max > 0;
  const w = 600;
  const h = 90;
  const padX = 4;
  const padY = 6;
  const innerW = w - padX * 2;
  const innerH = h - padY * 2;
  const n = Math.max(1, buckets.length);
  const slotW = innerW / n;
  const barGap = 1.5;
  const barW = Math.max(1, slotW - barGap);
  return (
    <section className="activity-chart">
      <header className="activity-chart-head">
        <h4>{title}</h4>
        <span className="activity-chart-total">{valueLabel}</span>
      </header>
      <div className="activity-chart-body">
        {hasData ? (
          <svg
            className="activity-chart-svg"
            viewBox={`0 0 ${w} ${h}`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            {buckets.map((b, i) => {
              const series = accessor(b);
              let yOffset = 0;
              return (
                <g key={i} transform={`translate(${padX + i * slotW + barGap / 2}, 0)`}>
                  {labels.map((label, li) => {
                    const v = series[label] ?? 0;
                    if (v <= 0) return null;
                    const segH = (v / max) * innerH;
                    const y = padY + (innerH - yOffset - segH);
                    yOffset += segH;
                    return (
                      <rect
                        key={label}
                        x={0}
                        y={y}
                        width={barW}
                        height={segH}
                        fill={palette[li]}
                      />
                    );
                  })}
                </g>
              );
            })}
          </svg>
        ) : (
          <p className="activity-chart-empty">No data in this window</p>
        )}
      </div>
    </section>
  );
}

