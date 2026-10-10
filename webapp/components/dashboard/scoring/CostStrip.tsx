"use client";
import { memo, useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered, seriesColor, useThemeVersion } from "@/lib/theme";
import { Badge, CardFrame } from "@/components/ui";
import {
  costSeries,
  readSpend,
  roundCosts,
  spendHeadline,
  spendLines,
} from "@/lib/derivations";
import { useCycleStream } from "@/lib/poll";

ensureChartRegistered();

export const CostStrip = memo(function CostStrip() {
  const { dash } = useCycleStream();
  useThemeVersion();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const rounds = useMemo(() => roundCosts(dash), [dash?.spend_metered_by_round]);
  const { metered } = readSpend(dash);

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
      tooltip: {
        callbacks: {
          afterBody: (items: { dataIndex: number }[]) => {
            const r = rounds[items[0]?.dataIndex ?? -1];
            if (!r) return "";
            return spendLines(r.metered)
              .filter((l) => l.kind.sent)
              .map((l) => `${l.label} prefix ${l.kind.prefix.badge}`);
          },
        },
      },
    },
    scales: {
      x: { stacked: true, ticks: { font: { size: 9 } }, grid: { display: false } },
      y: { stacked: true, ticks: { font: { size: 9 } } },
    },
  });

  return (
    <CardFrame
      title={<span>Cost by round</span>}
      actions={metered === null ? null : <Badge>{spendHeadline(metered)}</Badge>}
    >
      <div style={{ position: "relative", height: 140 }}>
        {rounds.length === 0 ? (
          <div
            style={{
              color: "var(--color-text-secondary)",
              fontSize: "var(--text-sm)",
              padding: 16,
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
