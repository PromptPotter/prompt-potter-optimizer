"use client";
import { memo, useMemo } from "react";
// `Chart`, not `Bar`: the cache overlay is a line dataset on a bar chart, and the per-type
// `Bar` wrapper types its data as bar-only. Both controllers register in `lib/theme.ts`.
import { Chart } from "react-chartjs-2";
import { ensureChartRegistered, getCss, useThemeVersion } from "@/lib/theme";
import { partialPanels, type HeadlineMetric } from "@/lib/derivations";
import type { MeasuredUnit } from "@/lib/api/types";
import { fmtSigned, unitCount } from "@/lib/format";
import { NOT_SEPARABLE, liftSeparates } from "@/lib/fitness";
import type { CandidateView } from "@/lib/types";
import {
  activeSeries,
  seriesByKey,
  seriesColumn,
  whiskerBands,
  type SeriesCtx,
  type SeriesKey,
  type SeriesSpec,
  type WhiskerBand,
} from "./series";
import type { ChartData, ChartOptions, ChartType, Plugin } from "chart.js";

ensureChartRegistered();

// Fractions, not pixels: a category centre's fraction is invariant under a width change, so a
// resize costs no React work.
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
    meanFitnessCiWhisker?: {
      anchor: SeriesKey | null;
      ciLo: (number | null)[];
      ciHi: (number | null)[];
    };
    xBridge?: { onGeometry: (g: PlotGeometry) => void };
  }
}

// One crown, on the parent only — the per-round crowns are the dendrogram's job.
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
    // Bars only: letting the cache line into the minimum drags the caption onto the dash.
    const topOf = (i: number): number => {
      let topY = Infinity;
      chart.data.datasets.forEach((_ds, di) => {
        const meta = chart.getDatasetMeta(di);
        if (meta.type !== "bar") return;
        const el = meta.data[i] as { y?: number } | undefined;
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
      ctx.fillStyle = getCss("--color-text-tertiary");
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

// Drawn at the anchor dataset's own rendered x, not the category centre, and on the axis that
// channel declares in `CANDIDATE_SERIES` — never a per-bar scale.
const ciWhiskerPlugin: Plugin<"bar", { bands: WhiskerBand[] }> = {
  id: "ciWhisker",
  afterDatasetsDraw(chart, _args, opts) {
    const bands = opts?.bands;
    if (!bands?.length) return;
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

// Canvas bars are out of CSS animation's reach, so this drives its own rAF redraw while `index` is set.
const PULSE_PERIOD_MS = 1600;
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
      const b = p?.base;
      const width = p?.width;
      if (
        typeof x !== "number" ||
        typeof y !== "number" ||
        typeof b !== "number" ||
        typeof width !== "number"
      ) {
        return;
      }
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

interface Props {
  views: CandidateView[];
  metrics: ReadonlySet<HeadlineMetric>;
  showMask: boolean;
  showCache: boolean;
  showOverlap: boolean;
  selectedKey: string | null;
  onSelect: (view: CandidateView | null) => void;
  divergenceBoundary: number | null;
  inFlightIndex: number | null;
  // MUST be stable: it rides the `options` memo.
  onGeometry: (g: PlotGeometry) => void;
  unit: MeasuredUnit;
  electedMetric: HeadlineMetric;
}

export const FitnessChart = memo(function FitnessChart({
  views,
  metrics,
  showMask,
  showCache,
  showOverlap,
  selectedKey,
  onSelect,
  divergenceBoundary,
  inFlightIndex,
  onGeometry,
  unit,
  electedMetric,
}: Props) {
  const themeVersion = useThemeVersion();
  const labels = useMemo(() => views.map((v) => v.label), [views]);

  const ctx = useMemo<SeriesCtx>(
    () => ({ metrics, showMask, showCache, showOverlap, views, unit, electedMetric }),
    [metrics, showMask, showCache, showOverlap, views, unit, electedMetric],
  );
  const active = useMemo(() => activeSeries(ctx), [ctx]);
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

  // The LAST crowned bar, not `findIndex`: every advancing round has a winner and the first is C0.
  const parentIdx = useMemo(() => {
    for (let i = views.length - 1; i >= 0; i--) if (views[i]?.is_winner) return i;
    return null;
  }, [views]);

  // The served lift, claimed only where its 95% interval excludes 0.
  const crown = useMemo(() => {
    const v = parentIdx == null ? undefined : views[parentIdx];
    const { matchedParentLift: lift, matchedParentLiftCiLo: lo, matchedParentLiftCiHi: hi } =
      v ?? {};
    if (lift == null || lo == null || hi == null || (lo <= 0 && hi >= 0)) return "";
    return ` ${fmtSigned(lift, 2)}`;
  }, [views, parentIdx]);

  const data = useMemo<ChartData<"bar" | "line">>(() => {
    const bars = active.filter((s) => s.kind === "bar").length;
    const cat = bars <= 1 ? 0.55 : bars === 2 ? 0.75 : bars === 3 ? 0.9 : 0.95;
    const barCount = Math.max(1, labels.length);
    const maxBar = Math.max(6, Math.min(28, Math.round(640 / (barCount * Math.max(1, bars)))));
    // The parent gets no ring: on the elected series its fill already IS the accent.
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
  }, [labels, views, active, ctx, themeVersion, selectionBorder]);

  const rotate = labels.length > ROTATE_THRESHOLD;
  const options = useMemo<ChartOptions<"bar">>(() => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
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
                color: getCss("--color-text-tertiary"),
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
            const n = views[idx]?.n_samples;
            if (n != null) {
              const exp = views[idx]?.n_expected;
              lines.push(
                exp != null && exp !== n
                  ? `${n} of ${unitCount(exp, unit)} scored`
                  : `${unitCount(n, unit)} scored`,
              );
            }
            const cached = views[idx]?.cached_samples;
            if (cached != null && cached > 0 && n != null) {
              lines.push(`${cached} of ${unitCount(n, unit)} from cache`);
            }
            const theta = views[idx]?.theta;
            if (typeof theta === "number") {
              const se = views[idx]?.theta_se;
              // Named "se", not ±: the drawn whisker is the wider 95% band.
              const tail = typeof se === "number" ? `, se ${se.toFixed(2)}` : "";
              lines.push(`ability θ ${theta.toFixed(2)}${tail} (elected on θ, not accuracy)`);
            }
            const ciLo = views[idx]?.meanFitnessCiLo;
            const ciHi = views[idx]?.meanFitnessCiHi;
            if (typeof ciLo === "number" && typeof ciHi === "number") {
              lines.push(`95% CI [${ciLo.toFixed(3)}, ${ciHi.toFixed(3)}]`);
            }
            const lift = views[idx]?.matchedParentLift;
            const lLo = views[idx]?.matchedParentLiftCiLo;
            const lHi = views[idx]?.matchedParentLiftCiHi;
            if (lift != null && lLo != null && lHi != null) {
              const flat = liftSeparates(lLo, lHi) ? "" : ` — ${NOT_SEPARABLE}`;
              lines.push(
                `lift vs parent ${fmtSigned(lift)} [${fmtSigned(lLo)}, ${fmtSigned(lHi)}]${flat}`,
              );
            }
            // Keyed on the election, not the round close: after it, no crown does mean "lost".
            if (views[idx]?.electionPending) {
              lines.push("no election yet — nothing crowned in this round");
            }
            return lines.join("\n");
          },
        },
      },
      barCaps: { counts: partialPanels(views), parent: parentIdx, crown },
      divergenceLine: { index: divergenceBoundary },
      inFlightPulse: { index: inFlightIndex },
      ciWhisker: { bands: whiskerBands(ctx) },
      xBridge: { onGeometry },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [themeVersion, ctx, rotate, views, selectedKey, onSelect, divergenceBoundary, inFlightIndex, showAbility, parentIdx, crown, onGeometry]);

  return (
    <div className="fitness-chart-frame">
      {/* Explicit type argument: `type="bar"` alone rejects the cache overlay's line dataset. */}
      <Chart<"bar" | "line">
        type="bar"
        data={data}
        options={options}
        plugins={CHART_PLUGINS}
        aria-label={`Candidates this round — ${views.length} bar${
          views.length === 1 ? "" : "s"
        } on ${showAbility ? "ability (θ)" : "composite fitness"}, each with its interval.`}
      />
    </div>
  );
});
