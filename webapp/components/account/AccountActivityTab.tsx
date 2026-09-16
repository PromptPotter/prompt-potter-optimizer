"use client";
// Activity pane — 3 vertically-stacked time-bucketed charts (spend / requests /
// tokens) over a selectable window, coloured by model or API key.

import { useState } from "react";
import { AccountEmpty, AccountFailure, AccountLoading } from "./AccountSection";
import { fmtCompact, fmtUsd } from "@/lib/format";
import { seriesColor, useThemeVersion } from "@/lib/theme";
import { useFetch } from "@/lib/hooks/useFetch";
import {
  fetchActivity,
  type ActivityBucket,
  type ActivityGroupBy,
  type ActivityResponse,
  type ActivityWindow,
} from "@/lib/api";

const WINDOW_LABEL: Record<ActivityWindow, string> = {
  "15m": "Past 15 min",
  "30m": "Past 30 min",
  "1h": "Past hour",
  "3h": "Past 3 hours",
  "1d": "Past day",
  "2d": "Past 2 days",
  "1w": "Past week",
  "1mo": "Past month",
  "1y": "Past year",
};
const WINDOW_ORDER = Object.keys(WINDOW_LABEL) as ActivityWindow[];

export function AccountActivityTab() {
  // The SVG paints literal fills, so a theme flip has to re-run this component to re-read them.
  useThemeVersion();
  const [window, setWindow] = useState<ActivityWindow>("1d");
  const [groupBy, setGroupBy] = useState<ActivityGroupBy>("model");
  // useFetch blanks data on the (window, group_by) key change in-render, so the
  // old buckets never render against the new axis labels — the reset is built in.
  const { data, error, kind } = useFetch(
    () => fetchActivity(window, groupBy),
    [window, groupBy],
  );

  return (
    <>
      <div className="activity-window-row">
        <label htmlFor="activity-window-select">Window</label>
        <select
          id="activity-window-select"
          value={window}
          onChange={(e) => setWindow(e.target.value as ActivityWindow)}
        >
          {WINDOW_ORDER.map((id) => (
            <option key={id} value={id}>
              {WINDOW_LABEL[id]}
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
      {error ? (
        <AccountFailure kind={kind} subject="activity" />
      ) : data === null ? (
        <AccountLoading subject="activity" />
      ) : data.total_requests === 0 ? (
        <AccountEmpty title={`No model calls in the ${WINDOW_LABEL[window].toLowerCase()}`}>
          Spend, requests and tokens appear here as soon as a campaign runs. Widen the window to
          look further back.
        </AccountEmpty>
      ) : (
        <ActivityCharts data={data} />
      )}
    </>
  );
}

function ActivityCharts({ data }: { data: ActivityResponse }) {
  const labels = data.series_labels;
  const palette = labels.map((_, i) => seriesColor(i));
  return (
    <>
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
      <ActivityBarChart
        title="Spend"
        valueLabel={fmtUsd(data.total_spend_usd)}
        buckets={data.buckets}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_spend}
        totalAccessor={(b) => b.spend_usd}
      />
      <ActivityBarChart
        title="Requests"
        valueLabel={data.total_requests.toLocaleString()}
        buckets={data.buckets}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_requests}
        totalAccessor={(b) => b.requests}
      />
      <ActivityBarChart
        title="Tokens"
        valueLabel={fmtCompact(data.total_tokens)}
        buckets={data.buckets}
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

