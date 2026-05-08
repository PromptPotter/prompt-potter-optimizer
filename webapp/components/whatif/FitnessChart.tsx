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
  stats?: {
    composite_fitness?: number;
    accuracy?: number;
    evaluators?: Record<string, number>;
  };
  samples?: string[];
}

// Pass-rate from in-flight HIT/MISS lines — proxy for accuracy while the
// candidate's full eval is still in progress and stats hasn't landed.
function runningAccuracy(cand: Candidate): number | null {
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

interface Props {
  candidates: Candidate[];
  selected: Set<string>;
  rows: Row[];
  nVariants: number;
  showComposite: boolean;
  showWhatIf: boolean;
  themeKey: string;
}

export function FitnessChart({
  candidates,
  selected,
  rows,
  nVariants,
  showComposite,
  showWhatIf,
  themeKey,
}: Props) {
  const total = Math.max(nVariants, 1);
  const labels = useMemo(() => Array.from({ length: total }, (_, i) => `C${i}`), [total]);

  // Two parallel arrays per series:
  //  *Raw — the true value (number or null) — drives the tooltip.
  //  *Plot — the value pushed to chart.js. Null is coerced to 0 (so
  //   `minBarLength` paints a stub) ONLY for candidates that have
  //   produced their first datum; candidates that haven't started stay
  //   null so chart.js leaves the slot blank — distinguishing "still
  //   computing" from "not yet started".
  const { accRaw, compRaw, whatifRaw, accPlot, compPlot, whatifPlot } = useMemo(() => {
    const aR: (number | null)[] = new Array(total).fill(null);
    const cR: (number | null)[] = new Array(total).fill(null);
    const wR: (number | null)[] = new Array(total).fill(null);
    const started: boolean[] = new Array(total).fill(false);
    for (const cand of candidates) {
      const i = Number(cand.idx);
      if (!Number.isFinite(i) || i < 0 || i >= total) continue;
      aR[i] = typeof cand.stats?.accuracy === "number" ? cand.stats.accuracy : runningAccuracy(cand);
      cR[i] = typeof cand.stats?.composite_fitness === "number" ? cand.stats.composite_fitness : null;
      wR[i] = correctedScore(cand, selected, rows);
      started[i] =
        (cand.samples?.length ?? 0) > 0 ||
        typeof cand.stats?.accuracy === "number" ||
        typeof cand.stats?.composite_fitness === "number" ||
        Object.keys(cand.stats?.evaluators ?? {}).length > 0;
    }
    const coerce = (v: number | null, i: number): number | null =>
      v == null ? (started[i] ? 0 : null) : v;
    return {
      accRaw: aR, compRaw: cR, whatifRaw: wR,
      accPlot: aR.map(coerce), compPlot: cR.map(coerce), whatifPlot: wR.map(coerce),
    };
  }, [candidates, selected, rows, total]);

  const data = useMemo<ChartData<"bar">>(() => {
    const seriesCount = 1 + (showComposite ? 1 : 0) + (showWhatIf ? 1 : 0);
    // Single bar centred per category; widen the group as series stack on.
    const cat = seriesCount === 1 ? 0.55 : seriesCount === 2 ? 0.75 : 0.9;
    const datasets: ChartData<"bar">["datasets"] = [
      {
        label: "accuracy",
        data: accPlot,
        backgroundColor: cssRgba("--color-accent-rgb", 0.95),
        borderColor: cssRgba("--color-accent-rgb", 0.95),
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: 28,
        minBarLength: 2,
      },
    ];
    if (showComposite) {
      datasets.push({
        label: "composite",
        data: compPlot,
        backgroundColor: cssRgba("--color-accent-rgb", 0.55),
        borderColor: cssRgba("--color-accent-rgb", 0.55),
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: 24,
        minBarLength: 2,
      });
    }
    if (showWhatIf) {
      const accentStrong = getCss("--color-accent-strong");
      datasets.push({
        label: "what-if",
        data: whatifPlot,
        backgroundColor: accentStrong,
        borderColor: accentStrong,
        borderWidth: 0,
        barPercentage: 0.95,
        categoryPercentage: cat,
        maxBarThickness: 24,
        minBarLength: 2,
      });
    }
    return { labels, datasets };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels, accPlot, compPlot, whatifPlot, showComposite, showWhatIf, themeKey]);

  const tooltipFor = (label: string, idx: number): string => {
    const src = label === "accuracy" ? accRaw : label === "composite" ? compRaw : whatifRaw;
    const v = src[idx];
    return `${label}: ${v == null ? "—" : v.toFixed(3)}`;
  };

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
          label: (ctx) => tooltipFor(String(ctx.dataset.label ?? ""), ctx.dataIndex),
        },
      },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [themeKey, accRaw, compRaw, whatifRaw]);

  return (
    <div className="fitness-chart-frame">
      <Bar key={themeKey} data={data} options={options} />
    </div>
  );
}
