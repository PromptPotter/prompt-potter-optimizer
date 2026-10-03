"use client";
import { memo, useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered, seriesColor, useThemeVersion } from "@/lib/theme";
import { Badge, CardFrame } from "@/components/ui";
import { roundCosts } from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { fmtUsd } from "@/lib/format";

ensureChartRegistered();

// What each round COST, on the trend's x-axis — its own strip, since dollars and fitness are
// different units. Stacked by bucket, never pooled; prefix-cache rides the tooltip (no dollars-saved is served).
export const CostStrip = memo(function CostStrip() {
  const { dash } = useDashboard();
  // Subscribe to the theme so a flip pulls fresh inks: a `<canvas>` has no cascade.
  useThemeVersion();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const rounds = useMemo(() => roundCosts(dash), [dash?.spend_by_round]);

  const labels = rounds.map((r) => String(r.round));
  const datasets = (rounds[0]?.buckets ?? []).map((b, i) => ({
    label: b.label,
    data: rounds.map((r) => r.buckets[i]?.usd ?? 0),
    backgroundColor: seriesColor(i),
    borderWidth: 0,
  }));

  const options = barChartDefaults({
    plugins: {
      legend: { display: true, labels: { boxWidth: 10, font: { size: 10 } } },
      tooltip: {
        callbacks: {
          // `c0%` and `c?` both print: a cold prefix is a measurement, an unreporting provider is not.
          afterBody: (items: { dataIndex: number }[]) => {
            const r = rounds[items[0]?.dataIndex ?? -1];
            if (!r) return "";
            return r.buckets
              .filter((b) => b.usd > 0 || b.write > 0)
              .map(
                (b) =>
                  `${b.label} prefix ${b.prefix.label}` +
                  (b.write > 0 ? ` · wrote ${b.write} tok` : ""),
              );
          },
        },
      },
    },
    scales: {
      x: { stacked: true, ticks: { font: { size: 9 } }, grid: { display: false } },
      y: { stacked: true, ticks: { font: { size: 9 } } },
    },
  });

  const total = rounds.reduce((acc, r) => acc + r.totalUsd, 0);

  return (
    <CardFrame
      title={<span>Cost by round</span>}
      actions={<Badge>{fmtUsd(total)}</Badge>}
    >
      <div style={{ position: "relative", height: 140 }}>
        {rounds.length === 0 ? (
          <div
            style={{
              color: "var(--color-text-tertiary)",
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
