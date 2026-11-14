import { memo, useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered } from "@/lib/chart";
import { fmtUsd } from "@/lib/format";
import { seriesColor, useThemeVersion } from "@/lib/theme";
import { CardFrame, ChartReading, useChartReading } from "@/components/ui";
import { costSeries, roundCosts } from "@/lib/derivations";
import { useViewedDashboard } from "@/lib/state/poll";

ensureChartRegistered();

// How the bill splits by round; the bill itself is the masthead's SPEND.
export const CostStrip = memo(function CostStrip() {
  const { dash } = useViewedDashboard();
  useThemeVersion();
  const { at, events } = useChartReading();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const rounds = useMemo(() => roundCosts(dash), [dash?.spend_metered_by_round]);

  const labels = rounds.map((r) => String(r.round));
  const series = costSeries(rounds);
  const datasets = series.map((s) => ({
    label: s.label,
    data: s.data,
    backgroundColor: seriesColor(s.ink),
    borderWidth: 0,
  }));

  const options = barChartDefaults({
    ...events,
    plugins: {
      legend: { display: true, labels: { boxWidth: 10, font: { size: 10 } } },
    },
    scales: {
      x: { stacked: true, ticks: { font: { size: 9 } }, grid: { display: false } },
      y: { stacked: true, ticks: { font: { size: 9 } } },
    },
  });

  const round = at === null ? undefined : rounds[at];
  const billed =
    at === null
      ? []
      : series.flatMap((s) => {
          const usd = s.data[at];
          return usd == null ? [] : [`${s.label.toLowerCase()} ${fmtUsd(usd)}`];
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
      {rounds.length > 0 && (
        <ChartReading hint="Hover or tap a round to read its bill.">
          {round === undefined
            ? null
            : `round ${round.round} · ${billed.join(" · ") || "nothing billed"}`}
        </ChartReading>
      )}
    </CardFrame>
  );
});
