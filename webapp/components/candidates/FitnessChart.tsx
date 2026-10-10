"use client";
import { memo, useMemo } from "react";
import { Chart } from "react-chartjs-2";
import { ensureChartRegistered, getCss, lineLook, lineMotion, useThemeVersion } from "@/lib/theme";
import { useMediaQuery } from "@/lib/hooks/useMediaQuery";
import { liftOf } from "@/lib/derivations";
import { fmtSigned, fmtTheta, unitCount } from "@/lib/format";
import { NOT_SEPARABLE } from "@/lib/fitness";
import type { CandidateBar } from "@/lib/types";
import {
  activeSeries,
  seriesByKey,
  seriesColumn,
  whiskerBands,
  type SeriesCtx,
  type SeriesSpec,
  type WhiskerBand,
} from "./series";
import type { Chart as ChartJS, ChartData, ChartOptions, ChartType, Plugin } from "chart.js";

ensureChartRegistered();

// Fractions, not pixels: invariant under a width change, so a resize costs no React work.
export interface PlotGeometry {
  left: number;
  rightGutter: number;
  centers: number[];
}

export function geomEqual(a: PlotGeometry | null, b: PlotGeometry): boolean {
  return (
    a != null &&
    a.left === b.left &&
    a.rightGutter === b.rightGutter &&
    a.centers.length === b.centers.length &&
    a.centers.every((v, i) => v === b.centers[i])
  );
}

declare module "chart.js" {
  // Type param arity must match chart.js's own declaration to merge; unused here.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface PluginOptionsByType<TType extends ChartType> {
    barCaps?: { counts: (number | null)[]; parent: number | null; crown: string };
    divergenceLine?: { index: number | null };
    inFlightPulse?: { index: number | null };
    ciWhisker?: { bands: WhiskerBand[]; shaded: boolean };
    xBridge?: { onGeometry: (g: PlotGeometry) => void };
  }
}

const CROWN = "♛";
const barCapsPlugin: Plugin<
  "bar",
  { counts: (number | null)[]; parent: number | null; crown: string }
> = {
  id: "barCaps",
  afterDatasetsDraw(chart, _args, opts) {
    const counts = opts?.counts;
    const xScale = chart.scales.x;
    if (!counts || !xScale) return;
    const { ctx, chartArea } = chart;
    // Bar channels only: letting the cache line into the minimum drags the caption onto the dash.
    const topOf = (i: number): number => {
      let topY = Infinity;
      chart.data.datasets.forEach((ds, di) => {
        if (seriesByKey(String(ds.label ?? ""))?.kind !== "bar") return;
        const el = chart.getDatasetMeta(di).data[i] as { y?: number } | undefined;
        if (el && typeof el.y === "number" && el.y < topY) topY = el.y;
      });
      return topY;
    };
    ctx.save();
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    const mono = getCss("--font-mono");
    counts.forEach((n, i) => {
      if (n == null) return;
      const topY = topOf(i);
      if (!Number.isFinite(topY)) return;
      ctx.font = `${getCss("--text-xs")} ${mono}`;
      ctx.fillStyle = getCss("--color-text-secondary");
      ctx.fillText(String(n), xScale.getPixelForValue(i), Math.max(topY - 4, chartArea.top + 10));
    });
    const w = opts?.parent;
    if (w != null && w >= 0) {
      const topY = topOf(w);
      if (Number.isFinite(topY)) {
        ctx.font = `12px ${mono}`;
        ctx.fillStyle = getCss("--color-accent");
        const capY = counts[w] != null ? topY - 15 : topY - 4;
        ctx.fillText(
          `${CROWN}${opts?.crown ?? ""}`,
          xScale.getPixelForValue(w),
          Math.max(capY, chartArea.top + 10),
        );
      }
    }
    ctx.restore();
  },
};

const BAND_ALPHA = 0.16;
type BandOpts = { bands: WhiskerBand[]; shaded: boolean };
type Placed = { getProps?: (p: string[], final: boolean) => Record<string, number> } | undefined;

function drawRibbons(chart: ChartJS, bands: WhiskerBand[]): void {
  const { ctx, chartArea } = chart;
  ctx.save();
  ctx.beginPath();
  ctx.rect(chartArea.left, chartArea.top, chartArea.width, chartArea.height);
  ctx.clip();
  ctx.globalAlpha = BAND_ALPHA;
  for (const band of bands) {
    const yScale = chart.scales[seriesByKey(band.anchor)?.axis ?? "y"];
    const di = chart.data.datasets.findIndex((ds) => ds.label === band.anchor);
    const ink = chart.data.datasets[di]?.borderColor;
    if (!yScale || di < 0 || typeof ink !== "string") continue;
    const points = chart.getDatasetMeta(di).data as Placed[];
    ctx.fillStyle = ink;
    let run: { x: number; lo: number; hi: number }[] = [];
    const close = () => {
      const first = run[0];
      if (first && run.length > 1) {
        ctx.beginPath();
        ctx.moveTo(first.x, first.hi);
        for (const p of run) ctx.lineTo(p.x, p.hi);
        for (let i = run.length - 1; i >= 0; i--) {
          const p = run[i];
          if (p) ctx.lineTo(p.x, p.lo);
        }
        ctx.closePath();
        ctx.fill();
      }
      run = [];
    };
    for (let i = 0; i < band.lo.length; i++) {
      const lo = band.lo[i];
      const hi = band.hi[i];
      const x = points[i]?.getProps?.(["x"], true)?.x;
      if (lo == null || hi == null || typeof x !== "number") {
        close();
        continue;
      }
      run.push({ x, lo: yScale.getPixelForValue(lo), hi: yScale.getPixelForValue(hi) });
    }
    close();
  }
  ctx.restore();
}

const ciWhiskerPlugin: Plugin<"bar", BandOpts> = {
  id: "ciWhisker",
  beforeDatasetsDraw(chart, _args, opts) {
    if (opts?.shaded && opts.bands?.length) drawRibbons(chart, opts.bands);
  },
  afterDatasetsDraw(chart, _args, opts) {
    const bands = opts?.bands;
    if (!bands?.length || opts?.shaded) return;
    const { ctx } = chart;
    ctx.save();
    ctx.strokeStyle = getCss("--color-ci");
    ctx.lineWidth = 1.5;
    const capHalf = 4;
    for (const band of bands) {
      const yScale = chart.scales[seriesByKey(band.anchor)?.axis ?? "y"];
      const meta = chart.data.datasets.findIndex((ds) => ds.label === band.anchor);
      if (!yScale || meta < 0) continue;
      const bars = chart.getDatasetMeta(meta);
      for (let i = 0; i < band.lo.length; i++) {
        const lo = band.lo[i];
        const hi = band.hi[i];
        if (lo == null || hi == null) continue;
        const el = bars.data[i] as
          | { getProps?: (p: string[], final: boolean) => Record<string, number> }
          | undefined;
        const x = el?.getProps?.(["x"], true)?.x;
        if (typeof x !== "number") continue;
        // No clamp: the server clips `mean_fitness_ci` to [0,1] and θ's axis is fitted to its data.
        const yLo = yScale.getPixelForValue(lo);
        const yHi = yScale.getPixelForValue(hi);
        ctx.beginPath();
        ctx.moveTo(x, yLo);
        ctx.lineTo(x, yHi);
        ctx.moveTo(x - capHalf, yLo);
        ctx.lineTo(x + capHalf, yLo);
        ctx.moveTo(x - capHalf, yHi);
        ctx.lineTo(x + capHalf, yHi);
        ctx.stroke();
      }
    }
    ctx.restore();
  },
};

const divergenceLinePlugin: Plugin<"bar", { index: number | null }> = {
  id: "divergenceLine",
  afterDatasetsDraw(chart, _args, opts) {
    const idx = opts?.index;
    if (idx == null || idx < 0) return;
    const xScale = chart.scales.x;
    if (!xScale) return;
    const { ctx, chartArea } = chart;
    const c = xScale.getPixelForValue(idx);
    let x: number;
    if (idx > 0) {
      x = (xScale.getPixelForValue(idx - 1) + c) / 2;
    } else {
      const step = xScale.getPixelForValue(1) - xScale.getPixelForValue(0);
      x = Math.max(chartArea.left, c - (Number.isFinite(step) ? step : 0) / 2);
    }
    const red = getCss("--color-danger");
    ctx.save();
    ctx.strokeStyle = red;
    ctx.lineWidth = 2;
    ctx.shadowColor = red;
    ctx.shadowBlur = 6;
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.stroke();
    ctx.restore();
  },
};

const PULSE_PERIOD_MS = 1600;
const POINT_PULSE_WIDTH = 10;
const pulseRaf = new WeakMap<object, number>();
const inFlightPulsePlugin: Plugin<"bar", { index: number | null }> = {
  id: "inFlightPulse",
  afterDatasetsDraw(chart, _args, opts) {
    const idx = opts?.index;
    const cancel = () => {
      const raf = pulseRaf.get(chart);
      if (raf != null) {
        cancelAnimationFrame(raf);
        pulseRaf.delete(chart);
      }
    };
    if (idx == null || idx < 0) {
      cancel();
      return;
    }
    const { ctx } = chart;
    if (!ctx) return;
    let left = Infinity;
    let right = -Infinity;
    let top = Infinity;
    let base = -Infinity;
    chart.data.datasets.forEach((_ds, di) => {
      const el = chart.getDatasetMeta(di).data[idx] as
        | { getProps?: (p: string[], final: boolean) => Record<string, number> }
        | undefined;
      const p = el?.getProps?.(["x", "y", "base", "width"], true);
      const x = p?.x;
      const y = p?.y;
      if (typeof x !== "number" || typeof y !== "number") return;
      const b = typeof p?.base === "number" ? p.base : chart.chartArea.bottom;
      const width = typeof p?.width === "number" ? p.width : POINT_PULSE_WIDTH;
      left = Math.min(left, x - width / 2);
      right = Math.max(right, x + width / 2);
      top = Math.min(top, y);
      base = Math.max(base, b);
    });
    if ([left, right, top, base].some((v) => !Number.isFinite(v))) {
      cancel();
      return;
    }
    const t = 0.5 + 0.5 * Math.sin((Date.now() / PULSE_PERIOD_MS) * Math.PI * 2);
    const glow = getCss("--color-success");
    ctx.save();
    ctx.strokeStyle = glow;
    ctx.globalAlpha = 0.55 + 0.4 * t;
    ctx.lineWidth = 1.5;
    ctx.shadowColor = glow;
    ctx.shadowBlur = 7 + 9 * t;
    ctx.strokeRect(left - 1.5, top - 1.5, right - left + 3, base - top + 3);
    ctx.restore();
    pulseRaf.set(
      chart,
      requestAnimationFrame(() => {
        if (chart.ctx) chart.draw();
      }),
    );
  },
  beforeDestroy(chart) {
    const raf = pulseRaf.get(chart);
    if (raf != null) cancelAnimationFrame(raf);
    pulseRaf.delete(chart);
  },
};

// `afterLayout`, not `afterDraw`: `inFlightPulse` re-enters `chart.draw()` at ~60fps.
const xBridgePlugin: Plugin<"bar", { onGeometry: (g: PlotGeometry) => void }> = {
  id: "xBridge",
  afterLayout(chart, _args, opts) {
    const emit = opts?.onGeometry;
    const x = chart.scales.x;
    if (!emit || !x) return;
    const { left, right, width } = chart.chartArea;
    // A zero-width (hidden) card keeps the last good geometry; re-showing fires a resize.
    if (!(width > 0)) return;
    const n = chart.data.labels?.length ?? 0;
    const centers: number[] = [];
    for (let i = 0; i < n; i++) centers.push((x.getPixelForValue(i) - left) / width);
    emit({ left, rightGutter: chart.width - right, centers });
  },
};

const CHART_PLUGINS = [
  barCapsPlugin,
  divergenceLinePlugin,
  inFlightPulsePlugin,
  ciWhiskerPlugin,
  xBridgePlugin,
];

const ROTATE_THRESHOLD = 8;
const LINES_ABOVE = 12;

interface Props {
  // MUST be memoized; the legend reads the same value (`useCandidatesModel::seriesCtx`).
  ctx: SeriesCtx;
  selectedKey: string | null;
  onSelect: (view: CandidateBar | null) => void;
  divergenceBoundary: number | null;
  inFlightIndex: number | null;
  onGeometry: (g: PlotGeometry) => void;
}

export const FitnessChart = memo(function FitnessChart({
  ctx,
  selectedKey,
  onSelect,
  divergenceBoundary,
  inFlightIndex,
  onGeometry,
}: Props) {
  const { views, metrics, unit } = ctx;
  const themeVersion = useThemeVersion();
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const labels = useMemo(() => views.map((v) => v.label), [views]);
  const asLines = labels.length > LINES_ABOVE;

  const active = useMemo(() => activeSeries(ctx), [ctx]);
  const bands = useMemo(() => whiskerBands(ctx), [ctx]);
  const showAbility = metrics.has("ability");

  const selectionBorder = useMemo(() => {
    if (selectedKey == null) return null;
    const idx = views.findIndex((v) => v.key === selectedKey);
    if (idx < 0) return null;
    const colour = getCss("--color-selection");
    return { idx, colour };
    // themeVersion gates getCss() — needed in deps; lint flags it unused.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [views, selectedKey, themeVersion]);

  const parentIdx = useMemo(() => {
    const idx = views.findIndex((v) => v.arm?.stands);
    return idx >= 0 ? idx : null;
  }, [views]);

  const crown = useMemo(() => {
    const lift = parentIdx == null ? null : liftOf(views[parentIdx]?.reading?.vs_reference ?? null);
    return lift == null || lift.side === "spans" ? "" : ` ${fmtSigned(lift.value, 2)}`;
  }, [views, parentIdx]);

  const cutCounts = useMemo(
    () => views.map((v) => (v.reading?.panel.cut ? v.reading.panel.scored : null)),
    [views],
  );

  const data = useMemo<ChartData<"bar" | "line">>(() => {
    const bars = active.filter((s) => s.kind === "bar").length;
    const cat = bars <= 1 ? 0.55 : bars === 2 ? 0.75 : bars === 3 ? 0.9 : 0.95;
    const barCount = Math.max(1, labels.length);
    const maxBar = Math.max(6, Math.min(28, Math.round(640 / (barCount * Math.max(1, bars)))));
    const outline = (spec: SeriesSpec, ink: string) => {
      const base = spec.hollow ? ink : "transparent";
      const baseW = spec.hollow ? 1.5 : 0;
      if (!selectionBorder) return { borderColor: base, borderWidth: baseW };
      return {
        borderColor: labels.map((_, i) =>
          i === selectionBorder.idx ? selectionBorder.colour : base,
        ),
        borderWidth: labels.map((_, i) => (i === selectionBorder.idx ? 3 : baseW)),
      };
    };
    return {
      labels,
      datasets: active.map((spec) => {
        const ink = getCss(spec.ink(ctx));
        const data = seriesColumn(spec, views);
        if (spec.kind === "line") {
          return {
            type: "line" as const,
            label: spec.key,
            data,
            borderColor: ink,
            borderDash: [6, 4],
            borderWidth: 1.5,
            pointRadius: 0,
            fill: false,
            spanGaps: false,
            yAxisID: spec.axis,
            // Lowest `order` paints LAST in chart.js — +1 would hide the line behind the bars.
            order: -1,
          };
        }
        if (asLines) {
          const picked = selectionBorder?.idx;
          return {
            type: "line" as const,
            label: spec.key,
            data,
            ...lineLook(ink, spec.hollow),
            pointRadius: labels.map((_, i) => (i === picked ? 5 : 2)),
            pointBorderColor: labels.map((_, i) =>
              i === picked && selectionBorder ? selectionBorder.colour : ink,
            ),
            pointHitRadius: 10,
            fill: false,
            spanGaps: spec.gap === "sparse",
            yAxisID: spec.axis,
          };
        }
        return {
          label: spec.key,
          data,
          backgroundColor: spec.hollow ? "transparent" : ink,
          ...outline(spec, ink),
          yAxisID: spec.axis,
          barPercentage: 0.95,
          categoryPercentage: cat,
          maxBarThickness: maxBar,
          // chart.js applies `minBarLength` as absolute, so a negative stub would cross zero.
          ...(spec.signed ? {} : { minBarLength: 2 }),
        };
      }),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [labels, views, active, ctx, themeVersion, selectionBorder, asLines]);

  const rotate = labels.length > ROTATE_THRESHOLD;
  const options = useMemo<ChartOptions<"bar">>(() => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: asLines ? lineMotion(reducedMotion) : false,
    onClick: (_evt, elements) => {
      const hit = elements?.[0];
      if (!hit) return;
      const view = views[hit.index];
      if (!view) return;
      if (view.key === selectedKey) onSelect(null);
      else onSelect(view);
    },
    onHover: (evt, elements) => {
      const target = (evt.native?.target ?? null) as HTMLElement | null;
      if (!target) return;
      target.style.cursor = elements?.[0] ? "pointer" : "default";
    },
    scales: {
      x: { grid: { display: false }, ticks: { color: (t) => getCss(t.index === parentIdx ? "--color-accent" : "--color-text-secondary"), font: (t) => ({ size: rotate ? 10 : 11, family: getCss("--font-mono"), weight: t.index === parentIdx ? "bold" as const : "normal" as const }), autoSkip: false, maxRotation: rotate ? 60 : 0, minRotation: rotate ? 60 : 0 } },
      y: { min: 0, max: 1, grid: { color: getCss("--color-border") }, ticks: { font: { size: 11 }, stepSize: 0.2 } },
      // Declared only while θ shows: the right gutter shifts every dendrogram fraction.
      ...(showAbility
        ? {
            y1: {
              position: "right" as const,
              grid: { display: false },
              ticks: { font: { size: 11 } },
              title: {
                display: true,
                text: "[θ]",
                align: "end" as const,
                color: getCss("--color-text-secondary"),
                font: { size: 11 },
              },
            },
          }
        : {}),
    },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (item) => {
            const v = views[item.dataIndex];
            const spec = seriesByKey(String(item.dataset.label ?? ""));
            return v && spec ? spec.tip(v, ctx) : "";
          },
          footer: (items) => {
            const idx = items[0]?.dataIndex;
            if (idx == null) return "";
            const lines: string[] = [];
            const bar = views[idx];
            const reading = bar?.reading;
            const n = reading?.panel.scored;
            if (reading && n != null) {
              const { expected: exp, cached } = reading.panel;
              lines.push(
                exp != null && exp !== n
                  ? `${n} of ${unitCount(exp, unit)} scored`
                  : `${unitCount(n, unit)} scored`,
              );
              if (cached != null && cached > 0) {
                lines.push(`${cached} of ${unitCount(n, unit)} from cache`);
              }
            }
            const theta = reading?.ability?.theta;
            if (typeof theta === "number") {
              const se = reading?.ability?.se;
              // Named "se", not ±: the drawn whisker is the wider 95% band.
              const tail = typeof se === "number" ? `, se ${se.toFixed(2)}` : "";
              lines.push(`ability θ ${fmtTheta(theta)}${tail} (elected on θ, not accuracy)`);
            }
            const ciLo = reading?.own?.accuracy?.ci_lo;
            const ciHi = reading?.own?.accuracy?.ci_hi;
            if (typeof ciLo === "number" && typeof ciHi === "number") {
              lines.push(`accuracy 95% CI [${ciLo.toFixed(3)}, ${ciHi.toFixed(3)}]`);
            }
            const lift = liftOf(reading?.vs_reference ?? null);
            if (lift != null && lift.ci_lo != null && lift.ci_hi != null) {
              const flat = lift.side === "spans" ? ` — ${NOT_SEPARABLE}` : "";
              lines.push(
                `accuracy lift vs parent ${fmtSigned(lift.value)} [${fmtSigned(lift.ci_lo)}, ${fmtSigned(lift.ci_hi)}]${flat}`,
              );
            }
            if (bar?.arm && !bar.arm.reading.election.held) {
              lines.push("no election yet — nothing crowned in this round");
            }
            return lines.join("\n");
          },
        },
      },
      barCaps: { counts: cutCounts, parent: parentIdx, crown },
      divergenceLine: { index: divergenceBoundary },
      inFlightPulse: { index: inFlightIndex },
      ciWhisker: { bands, shaded: asLines },
      xBridge: { onGeometry },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [themeVersion, ctx, rotate, asLines, reducedMotion, views, selectedKey, onSelect, divergenceBoundary, inFlightIndex, showAbility, parentIdx, crown, cutCounts, onGeometry, bands]);

  return (
    <div className="fitness-chart-frame">
      <Chart<"bar" | "line">
        type="bar"
        data={data}
        options={options}
        plugins={CHART_PLUGINS}
        aria-label={`Candidates this round — ${views.length} ${asLines ? "point" : "bar"}${
          views.length === 1 ? "" : "s"
        } per series (${active.map((s) => s.key).join(", ")}); intervals on ${
          bands.map((b) => b.anchor).join(" and ") || "none"
        }.`}
      />
    </div>
  );
});
