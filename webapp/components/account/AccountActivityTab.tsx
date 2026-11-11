import { useState } from "react";
import { Empty, ReadState, Select, type SelectOption } from "@/components/ui";
import { billText, ratePricedText } from "@/lib/derivations";
import { fmtCompact } from "@/lib/format";
import { seriesVar } from "@/lib/theme";
import { useRead } from "@/lib/hooks/useRead";
import { activityRead } from "@/lib/api";
import {
  type ActivityBucket,
  type ActivityGroupBy,
  type ActivityResponse,
  type ActivityWindow,
  ACTIVITY_WINDOW_INFO,
} from "@/lib/api/types";
import { wordKeys } from "@/lib/api/words";

const WINDOW_ORDER = wordKeys(ACTIVITY_WINDOW_INFO);
const GROUP_BY_OPTIONS: readonly SelectOption<ActivityGroupBy>[] = [
  { value: "model", label: "By Model" },
  { value: "api_key", label: "By API Key" },
];

export function AccountActivityTab() {
  const [window, setWindow] = useState<ActivityWindow>("1d");
  const [groupBy, setGroupBy] = useState<ActivityGroupBy>("model");
  const read = useRead(activityRead(window, groupBy));

  return (
    <>
      <div className="activity-window-row">
        <label htmlFor="activity-window-select">Window</label>
        <Select
          id="activity-window-select"
          value={window}
          options={WINDOW_ORDER.map((id) => ({ value: id, label: ACTIVITY_WINDOW_INFO[id].label }))}
          onChange={setWindow}
        />
        <label htmlFor="activity-group-select">Color by</label>
        <Select
          id="activity-group-select"
          value={groupBy}
          options={GROUP_BY_OPTIONS}
          onChange={setGroupBy}
        />
      </div>
      <ReadState
        read={read}
        subject="activity"
        empty={(data) =>
          data.total_requests === 0 ? (
            <Empty title={`No model calls in the ${ACTIVITY_WINDOW_INFO[window].label.toLowerCase()}`}>
              Spend, requests and tokens appear here as soon as a campaign runs. Widen the window
              to look further back.
            </Empty>
          ) : null
        }
      >
        {(data) => <ActivityCharts data={data} />}
      </ReadState>
    </>
  );
}

function ActivityCharts({ data }: { data: ActivityResponse }) {
  const labels = data.series_labels;
  const palette = labels.map((_, i) => seriesVar(i));
  const priced = ratePricedText(data.total_rate_priced_usd, data.calls_rate_priced);
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
        valueLabel={
          billText(data.total_spend_usd, data.bill_is_floor) + (priced ? ` + ${priced}` : "")
        }
        buckets={data.buckets}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_spend}
        peak={data.peak_spend_usd}
      />
      <ActivityBarChart
        title="Requests"
        valueLabel={data.total_requests.toLocaleString()}
        buckets={data.buckets}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_requests}
        peak={data.peak_requests}
      />
      <ActivityBarChart
        title="Tokens"
        valueLabel={fmtCompact(data.total_tokens)}
        buckets={data.buckets}
        labels={labels}
        palette={palette}
        accessor={(b) => b.series_tokens}
        peak={data.peak_tokens}
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
  peak: number | null;
}

function ActivityBarChart({
  title,
  valueLabel,
  buckets,
  labels,
  palette,
  accessor,
  peak,
}: ActivityBarChartProps) {
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
        {peak !== null ? (
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
                    const v = series[label];
                    if (v === undefined || v <= 0) return null;
                    const segH = (v / peak) * innerH;
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
          <Empty className="activity-chart-empty">No data in this window</Empty>
        )}
      </div>
    </section>
  );
}

