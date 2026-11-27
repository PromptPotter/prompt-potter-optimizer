import { memo } from "react";
import { Bar } from "react-chartjs-2";
import type { ChartOptions } from "chart.js";
import { CHART_DEFAULTS, ensureChartRegistered } from "@/lib/chart";
import { seriesColor, useThemeVersion } from "@/lib/theme";
import { CardFrame, ChartReading, useChartReading } from "@/components/ui";
import { useViewedDashboard } from "@/lib/state/poll";

ensureChartRegistered();

const NO_SPEND = { rounds: [], series: [], readings: [] } as const;

// How the bill splits by round, as served (`SpendByRound`); the bill itself is the masthead's SPEND.
export const CostStrip = memo(function CostStrip() {
  const { dash } = useViewedDashboard();
  useThemeVersion();
  const { rounds, series, readings } = dash?.spend_by_round ?? NO_SPEND;
  const { at, events, frame } = useChartReading(rounds.length);

  const labels = rounds.map(String);
  // A kind's ink is its place in the served order, every kind served.
  const datasets = series.map((s, ink) => ({
    label: s.label,
    data: s.billed_usd,
    backgroundColor: seriesColor(ink),
    borderWidth: 0,
  }));

  const options: ChartOptions<"bar"> = {
    ...CHART_DEFAULTS,
    ...events,
    plugins: {
      legend: { display: true, labels: { boxWidth: 10, font: { size: 10 } } },
    },
    scales: {
      x: { stacked: true, ticks: { font: { size: 9 } }, grid: { display: false } },
      y: { stacked: true, ticks: { font: { size: 9 } } },
    },
  };


  return (
    <CardFrame title={<span>Spend by round</span>}>
      <div className="chart-frame" {...frame}>
        {rounds.length === 0 ? (
          <div
            style={{
              color: "var(--color-text-secondary)",
              fontSize: "var(--text-sm)",
              padding: "var(--space-16)",
            }}
          >
            Cost lands per round as calls bill. Nothing has been spent yet.
          </div>
        ) : (
          <Bar
            data={{ labels, datasets }}
            options={options}
            aria-label="Spend per round, stacked by the buckets that round carried"
          />
        )}
      </div>
      {rounds.length > 0 && (
        <ChartReading hint="Hover, tap or press ← → on the chart to read a round's bill.">
          {at === null ? null : (readings[at] ?? null)}
        </ChartReading>
      )}
    </CardFrame>
  );
});
