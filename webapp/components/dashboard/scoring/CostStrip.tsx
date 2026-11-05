import { memo, useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered, seriesColor, useThemeVersion } from "@/lib/theme";
import { CardFrame } from "@/components/ui";
import { costSeries, roundCosts } from "@/lib/derivations";
import { useViewedDashboard } from "@/lib/poll";

ensureChartRegistered();

// How the bill splits by round; the bill itself is the masthead's SPEND.
export const CostStrip = memo(function CostStrip() {
  const { dash } = useViewedDashboard();
  useThemeVersion();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const rounds = useMemo(() => roundCosts(dash), [dash?.spend_metered_by_round]);

  const labels = rounds.map((r) => String(r.round));
  const datasets = costSeries(rounds).map((s) => ({
    label: s.label,
    data: s.data,
    backgroundColor: seriesColor(s.ink),
    borderWidth: 0,
  }));

  const options = barChartDefaults({
    plugins: {
      legend: { display: true, labels: { boxWidth: 10, font: { size: 10 } } },
    },
    scales: {
      x: { stacked: true, ticks: { font: { size: 9 } }, grid: { display: false } },
      y: { stacked: true, ticks: { font: { size: 9 } } },
    },
  });

  return (
    <CardFrame title={<span>Spend by round</span>}>
      <div className="chart-frame">
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
    </CardFrame>
  );
});
