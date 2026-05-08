"use client";
import { useMemo } from "react";
import { Bar } from "react-chartjs-2";
import { ensureChartRegistered } from "@/lib/chart-init";
import { cssRgba, getCss } from "@/lib/theme";
import { parseSampleLine } from "@/lib/sample-line";
import type { ChartData, ChartOptions } from "chart.js";
import type { Row } from "./meta";

ensureChartRegistered();

interface Candidate {
  idx?: number;
  stats?: { composite_fitness?: number; evaluators?: Record<string, number> };
  samples?: string[];
}

// Running accuracy from in-flight HIT/MISS lines — proxy for composite_fitness
// while the candidate's full eval is still in progress and stats hasn't landed.
function runningActual(cand: Candidate): number | null {
  const samples = cand.samples ?? [];
  if (samples.length === 0) return null;
  let hits = 0;
  let total = 0;
  for (const raw of samples) {
    const p = parseSampleLine(raw);
    if (p.status === "HIT") { hits += 1; total += 1; }
    else if (p.status === "MISS") { total += 1; }
  }
  return total > 0 ? hits / total : null;
}

interface Props {
  candidates: Candidate[];
  selected: Set<string>;
  rows: Row[];
  nVariants: number;
  themeKey: string;
}

function correctedScore(cand: Candidate, selected: Set<string>, rows: Row[]): number | null {
  const ev = cand.stats?.evaluators ?? {};
  let sum = 0;
  let n = 0;
  for (const sel of selected) {
    const v = ev[sel];
    if (v == null) continue;
    const direction = rows.find((r) => r.displayName === sel)?.direction ?? "high";
    sum += direction === "low" ? 1 - v : v;
    n += 1;
  }
  return n > 0 ? sum / n : null;
}

function pickWinner(lines: { idx: number; v: number | null }[]): number | null {
  let best: number | null = null;
  let bestVal = -Infinity;
  for (const l of lines) {
    if (l.v == null) continue;
    if (l.v > bestVal) { bestVal = l.v; best = l.idx; }
  }
  return best;
}

export function WhatIfStats({ candidates, selected, rows, nVariants, themeKey }: Props) {
  const total = Math.max(nVariants, 1);
  const labels = useMemo(() => Array.from({ length: total }, (_, i) => `C${i}`), [total]);

  const { actualData, whatifData, lines } = useMemo(() => {
    const a: (number | null)[] = new Array(total).fill(null);
    const w: (number | null)[] = new Array(total).fill(null);
    const ls: { idx: number; actual: number | null; whatif: number | null }[] = [];
    for (const c of candidates) {
      const i = Number(c.idx);
      if (!Number.isFinite(i) || i < 0 || i >= total) continue;
      const actual = typeof c.stats?.composite_fitness === "number"
        ? c.stats.composite_fitness
        : runningActual(c);
      const whatif = correctedScore(c, selected, rows);
      a[i] = actual;
      w[i] = whatif;
      ls.push({ idx: i, actual, whatif });
    }
    return { actualData: a, whatifData: w, lines: ls.filter((l) => l.actual != null || l.whatif != null) };
  }, [candidates, selected, rows, total]);

  let winnerLine: React.ReactNode;
  if (lines.length === 0) {
    winnerLine = <span className="same">no candidates yet</span>;
  } else if (selected.size === 0) {
    winnerLine = <span className="same">no evaluators selected</span>;
  } else {
    const wA = pickWinner(lines.map((l) => ({ idx: l.idx, v: l.actual })));
    const wW = pickWinner(lines.map((l) => ({ idx: l.idx, v: l.whatif })));
    if (wW == null) {
      winnerLine = <span className="same">what-if not computable on these candidates</span>;
    } else if (wA != null && wA !== wW) {
      winnerLine = <span className="swap">winner flips C{wA} → C{wW}</span>;
    } else {
      winnerLine = <span className="same">winner unchanged · C{wW}</span>;
    }
  }

  // Bars: pair of thin sticks per candidate. Transparent amber = actual,
  // solid amber = what-if. Lifted from vanilla whatifRenderChart.
  const data = useMemo<ChartData<"bar">>(() => ({
    labels,
    datasets: [
      {
        label: "actual",
        data: actualData as number[],
        backgroundColor: cssRgba("--color-accent-rgb", 0.55),
        borderColor: cssRgba("--color-accent-rgb", 0.55),
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: 0.45,
        maxBarThickness: 7,
        minBarLength: 2,
      },
      {
        label: "what-if",
        data: whatifData as number[],
        backgroundColor: cssRgba("--color-accent-rgb", 0.95),
        borderColor: cssRgba("--color-accent-rgb", 0.95),
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: 0.45,
        maxBarThickness: 7,
      },
    ],
    // re-derive on theme change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [labels, actualData, whatifData, themeKey]);

  const options = useMemo<ChartOptions<"bar">>(() => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 200 },
    scales: {
      x: { grid: { display: false }, ticks: { font: { size: 12, family: "Cascadia Mono, SF Mono, Menlo, Consolas, monospace" }, autoSkip: false } },
      y: { min: 0, max: 1, grid: { color: getCss("--color-border-tertiary") }, ticks: { font: { size: 11 }, stepSize: 0.25 } },
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (ctx) => `${ctx.dataset.label}: ${(ctx.parsed.y ?? 0).toFixed(3)}`,
        },
      },
    },
    // re-derive on theme change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [themeKey]);

  return (
    <aside className="whatif-stats">
      <div className="whatif-stats-title">Per-candidate fitness</div>
      <div className="whatif-legend-inline">
        <span><span className="dot actual" />actual</span>
        <span><span className="dot whatif" />what-if</span>
      </div>
      <div className="whatif-stats-winner">{winnerLine}</div>
      <div className="whatif-stats-chart">
        {/* Always render the chart frame with assumed C0..Cn labels (driven
            by n_variants). Empty datasets paint flat 0-axes so the operator
            sees the slot count immediately. */}
        <Bar key={themeKey} data={data} options={options} />
      </div>
    </aside>
  );
}
