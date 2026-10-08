"use client";
import { memo, useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered, seriesColor, useThemeVersion } from "@/lib/theme";
import { Badge, CardFrame } from "@/components/ui";
import { SPEND_BUCKETS, costSeries, prefixReading, roundCosts, spendLines } from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { fmtUsd } from "@/lib/format";

ensureChartRegistered();

// What each round COST, on the trend's x-axis — its own strip, since dollars and fitness are
// different units. Stacked by kind, never pooled; prefix-cache rides the tooltip (no dollars-saved is served).
export const CostStrip = memo(function CostStrip() {
  const { dash } = useDashboard();
  // Subscribe to the theme so a flip pulls fresh inks: a `<canvas>` has no cascade.
  useThemeVersion();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const rounds = useMemo(() => roundCosts(dash), [dash?.spend_metered_by_round]);
  const billed = dash?.spend_metered?.billed_usd ?? null;

  const labels = rounds.map((r) => String(r.round));
  // A kind keeps its ink whichever kinds a run carries.
  const datasets = costSeries(rounds).map((s) => ({
    label: s.label,
    data: s.data,
    backgroundColor: seriesColor(SPEND_BUCKETS.findIndex((b) => b.key === s.key)),
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
            return spendLines(r.metered)
              .filter((l) => l.kind.billed_usd > 0 || l.kind.cache_write_tokens > 0)
              .map(
                (l) =>
                  `${l.label} prefix ${prefixReading(l.kind.cache_share, false).label}` +
                  (l.kind.cache_write_tokens > 0 ? ` · wrote ${l.kind.cache_write_tokens} tok` : ""),
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

  return (
    <CardFrame
      title={<span>Cost by round</span>}
      actions={billed === null ? null : <Badge>{fmtUsd(billed)}</Badge>}
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
