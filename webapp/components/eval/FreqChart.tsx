"use client";
import { useEffect, useRef } from "react";
import { Bar } from "react-chartjs-2";
import { barChartDefaults, ensureChartRegistered } from "@/lib/chart-config";
import { getCss } from "@/lib/theme";
import { TERMS } from "@/lib/terms";
import { parseSampleLine } from "@/lib/sample-line";
import { liveL1Candidates, type DashboardSnapshot } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";
import { useRoundFile } from "@/lib/useRoundFile";
import { CardFrame } from "@/components/ui/card";

ensureChartRegistered();

interface ResultRow {
  score?: number;
  error?: unknown;
  predicted?: string;
  ground_truth?: string;
}

interface Props {
  dash: DashboardSnapshot | null;
  themeKey: string;
}

const LABELS = ["0", "", "", "", "", "", "", "", "", "1"];

function bucketScores(results: ResultRow[]): number[] {
  const buckets = new Array<number>(10).fill(0);
  results.forEach((r) => {
    const score = typeof r.score === "number"
      ? r.score
      : (r.error
        ? 0
        : (r.ground_truth && r.predicted && String(r.predicted).includes(String(r.ground_truth).replace("#### ", "").trim()) ? 1 : 0));
    const idx = Math.min(9, Math.max(0, Math.floor(score * 9.999)));
    buckets[idx] += 1;
  });
  return buckets;
}

// Parse the live sample lines from in-flight candidates into pseudo-results
// so the chart can bucket per-sample HIT/MISS without waiting for round
// completion.
function liveResultsFrom(dash: DashboardSnapshot | null): ResultRow[] {
  const out: ResultRow[] = [];
  for (const c of liveL1Candidates(dash)) {
    for (const raw of c.samples ?? []) {
      if (typeof raw !== "string") continue;
      const p = parseSampleLine(raw);
      if (p.status === "HIT") out.push({ score: 1 });
      else if (p.status === "MISS") out.push({ score: 0 });
    }
  }
  return out;
}

export function FreqChart({ dash, themeKey }: Props) {
  const chartRef = useRef(null);
  const { campaignId, cycleId } = useWorkspace();

  // Latest completed round from the dashboard summary. `dash.rounds` is sorted
  // ascending — the last entry is the most recent. Null until the first round
  // closes; the lazy fetch returns EMPTY in that window and the live bucket
  // path below carries the chart.
  const rounds = dash?.rounds ?? [];
  const latestRound = rounds.length > 0 ? rounds[rounds.length - 1].round : null;
  const { doc: latestRoundDoc } = useRoundFile(campaignId, cycleId, latestRound);
  const latestResults = (latestRoundDoc?.results as ResultRow[] | undefined) ?? [];

  const live = liveResultsFrom(dash);
  const useLive = live.length > 0;
  const data = bucketScores(useLive ? live : latestResults);
  const accStrong = getCss("--color-accent-strong");
  const acc = getCss("--color-accent");
  const colors = data.map((_, i) => (i < 5 ? accStrong : acc));

  // re-derive on theme change
  useEffect(() => {}, [themeKey]);

  const chartData = {
    labels: LABELS,
    datasets: [{ data, backgroundColor: colors, borderRadius: 2 }],
  };
  const options = barChartDefaults({
    plugins: { legend: { display: false } },
    scales: { x: { display: false }, y: { display: false } },
  });

  return (
    <CardFrame
      title={<span title={TERMS.stub_score_freq}>Score Frequency</span>}
      actions={<span className="badge">{useLive ? "live" : "round"}</span>}
    >
      <div style={{ position: "relative", height: 140 }}>
        <Bar key={themeKey} ref={chartRef} data={chartData} options={options} />
      </div>
    </CardFrame>
  );
}
