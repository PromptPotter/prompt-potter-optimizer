"use client";
import { memo, useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered, seriesColor, useThemeVersion } from "@/lib/theme";
import { CardFrame } from "@/components/ui";
import { roundCosts } from "@/lib/derivations";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { fmtUsd } from "@/lib/format";

ensureChartRegistered();

// What each round COST, on the same x-axis as the trend beside it.
//
// Its own strip rather than a channel on the candidates bar chart, and that is the whole design
// decision: dollars and fitness are different units, and the candidates chart is already carrying
// up to seven series, two whisker bands, a crown-with-lift caption, partial-panel counts, a
// divergence divider and an animated in-flight pulse — `barCaps` owns both text rows above every
// bar and there is no annotation slot left. A cost bar seated beside a fitness bar also invites
// reading them as comparable, which they are not.
//
// STACKED BY BUCKET, never pooled: the three run different prompts against different providers
// (`derivations/spend.ts` records the measured case), so "where did round 3's money go" is a
// three-part answer or it is not an answer.
//
// The prefix-cache reading rides the TOOLTIP rather than a lighter portion of each bar. A drawn
// "discounted portion" would need dollars-SAVED, and nothing serves that: `used_usd` is the bill
// with the discount already applied, and turning a token-share into a dollar-share here would be
// exactly the client-side arithmetic `webapp/CLAUDE.md` § Scoring authority forbids.
export const CostStrip = memo(function CostStrip() {
  const { dash } = useDashboard();
  // Subscribe to the theme so a flip re-runs this and pulls fresh canvas inks; a `<canvas>` has
  // no cascade to read a `var()` off.
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
          // The prefix reading per bucket, on the round the operator is pointing at — which is the
          // question the cost raises. `c0%` and `c?` are printed, not suppressed: a cold prefix is
          // a measurement and an unreporting provider is a third thing, and collapsing them is
          // what let a bucket sit at 0% capture looking exactly like a warm one.
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
      actions={<span className="badge">{fmtUsd(total)}</span>}
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
            aria-label="Spend per round, stacked by backend, optimizer loop and judge"
          />
        )}
      </div>
    </CardFrame>
  );
});
