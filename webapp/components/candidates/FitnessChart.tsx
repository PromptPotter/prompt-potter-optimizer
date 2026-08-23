"use client";
import { memo, useMemo } from "react";
// `Chart`, not `Bar`: the cache overlay is a line dataset on a bar chart, and the per-type
// `Bar` wrapper types its data as bar-only. Both controllers register in `lib/theme.ts`.
import { Chart } from "react-chartjs-2";
import { ensureChartRegistered, getCss, useThemeVersion } from "@/lib/theme";
import { partialPanels, type HeadlineMetric } from "@/lib/derivations";
import type { MeasuredUnit } from "@/lib/api/types";
import { fmtSigned, unitCount } from "@/lib/format";
import type { CandidateView } from "@/lib/types";
import {
  activeSeries,
  seriesByKey,
  seriesColumn,
  whiskerAnchor,
  type SeriesCtx,
  type SeriesKey,
  type SeriesSpec,
} from "./series";
import type { ChartData, ChartOptions, ChartType, Plugin } from "chart.js";

ensureChartRegistered();

// Published plot geometry — the bridge that lets the dendrogram strip, an SVG
// sibling rendered UNDER this canvas, land its nodes exactly on the bar category
// centers.
//
// FRACTION space, deliberately. chart.js's category scale is linear in the plot
// width (the bar controller sets `offset:true` on the index scale), so a centre's
// fraction is INVARIANT under a pure width change — only the two px gutters and
// the bar COUNT can move it. A window resize, What-If opening, or the sidebar
// collapsing therefore costs no React work at all: the strip's percentage
// coordinates track the canvas through the browser's own layout pass, in the same
// frame. Publishing raw pixels would re-render on every resize tick and still
// leave the genealogy one frame behind the bars mid-drag.
export interface PlotGeometry {
  // px inset, canvas left edge → plot area.
  left: number;
  // px inset, plot area's right edge → canvas right edge.
  rightGutter: number;
  // Category centre i, as a fraction of the plot width.
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
    barCaps?: { counts: (number | null)[]; incumbent: number | null; crown: string };
    divergenceLine?: { index: number | null };
    inFlightPulse?: { index: number | null };
    meanFitnessCiWhisker?: {
      // The series the band brackets, or null when it is not on screen. A KEY, not a
      // literal — this join used to be the string "accuracy", so turning that series off
      // made every band vanish with nothing on screen to say so.
      anchor: SeriesKey | null;
      ciLo: (number | null)[];
      ciHi: (number | null)[];
    };
    xBridge?: { onGeometry: (g: PlotGeometry) => void };
  }
}

// The strip above the bars — one pass, because both marks answer "what does this group need
// said above it": the CROWN on the incumbent (one mark, not one per advancing round; the
// per-round crowns are the dendrogram's job), and the sample COUNT only where
// `partialPanels` judges it news.
const CROWN = "♛";
const barCapsPlugin: Plugin<
  "bar",
  { counts: (number | null)[]; incumbent: number | null; crown: string }
> = {
  id: "barCaps",
  afterDatasetsDraw(chart, _args, opts) {
    const counts = opts?.counts;
    const xScale = chart.scales.x;
    if (!counts || !xScale) return;
    const { ctx, chartArea } = chart;
    // Highest (smallest y) bar top across this candidate's group. BARS only — letting the
    // cache line into the minimum drags the caption onto the dash.
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
    const w = opts?.incumbent;
    if (w != null && w >= 0) {
      const topY = topOf(w);
      if (Number.isFinite(topY)) {
        ctx.font = `12px ${mono}`;
        ctx.fillStyle = getCss("--color-accent");
        // Above the count when both land on one group — the crown is the louder fact.
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

// Error-bar whisker — a vertical line from `ciLo` to `ciHi` (mapped value→pixel via the left
// [0,1] y scale) with short end-caps, so no point estimate stands alone. Drawn at the bracketed
// dataset's OWN rendered x (which shifts within the bar group depending on how many series
// show), not the category center — a whisker must sit on the bar it brackets. Bars with a null
// CI are skipped.
//
// ONE band per candidate, on ONE percent-axis bar. There is no scale to resolve: a single
// writer stamps it when the candidate finishes scoring. Do not reintroduce a per-bar scale;
// make the one band mean one thing instead.
//
// WHICH bar is `whiskerAnchor(ctx)`, and it is a `SeriesKey` for a reason. This used to be
// the bare literal `"accuracy"`, so turning that one series off left every band undrawn with
// nothing on screen to say so — the second time this exact failure shipped. A key cannot
// drift from the dataset it names without the compiler noticing.
const meanFitnessCiWhiskerPlugin: Plugin<
  "bar",
  { anchor: SeriesKey | null; ciLo: (number | null)[]; ciHi: (number | null)[] }
> = {
  id: "meanFitnessCiWhisker",
  afterDatasetsDraw(chart, _args, opts) {
    const ciLo = opts?.ciLo;
    const ciHi = opts?.ciHi;
    const yScale = chart.scales.y;
    if (!ciLo || !ciHi || !yScale || !opts?.anchor) return;
    const meta = chart.data.datasets.findIndex((ds) => ds.label === opts.anchor);
    if (meta < 0) return;
    const bars = chart.getDatasetMeta(meta);
    const { ctx } = chart;
    ctx.save();
    ctx.strokeStyle = getCss("--color-ci");
    ctx.lineWidth = 1.5;
    const capHalf = 4;
    for (let i = 0; i < ciLo.length; i++) {
      const lo = ciLo[i];
      const hi = ciHi[i];
      if (lo == null || hi == null) continue;
      const el = bars.data[i] as
        | { getProps?: (p: string[], final: boolean) => Record<string, number> }
        | undefined;
      const x = el?.getProps?.(["x"], true)?.x;
      if (typeof x !== "number") continue;
      // Bounded to [0,1] by the server (`scoring/selection.py::mean_fitness_ci` clips to its
      // support), so the pixel always lands inside the fixed axis. This used to clamp to the
      // plot area, compensating here for an interval that claimed negative accuracy.
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
    ctx.restore();
  },
};

// Red vertical divider at the mask divergence boundary: bars left of it are the
// invariant prefix (the masked criterion would have elected the SAME winners up
// to here), bars at/right of it are counterfactual. Drawn at the LEFT edge of the
// first divergent bar — "before the divergent values, after what is truly the
// same". Index null ⇒ no mask active / no divergence ⇒ nothing drawn.
const divergenceLinePlugin: Plugin<"bar", { index: number | null }> = {
  id: "divergenceLine",
  afterDatasetsDraw(chart, _args, opts) {
    const idx = opts?.index;
    if (idx == null || idx < 0) return;
    const xScale = chart.scales.x;
    if (!xScale) return;
    const { ctx, chartArea } = chart;
    // Left edge of category `idx` = midpoint to its left neighbour; for idx 0,
    // step half a category left of the first centre (clamped to the plot edge).
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

// The in-flight candidate's bar pulses a green glow around its outline while it
// is still accumulating samples — the same success-green glow the optimizer
// canvas casts around the active node, here hugging the bar's box (no
// background fill, just the glow around the box). Bars are canvas-drawn (not
// DOM), so a CSS animation can't reach them: this plugin strokes the glow in
// `afterDatasetsDraw` (on top of the bars) and drives its own redraw via
// requestAnimationFrame. The loop runs ONLY while `index` is a real bar (a
// candidate is scoring) and cancels itself the moment scoring ends or the chart
// is destroyed — no standing animation against `animation:false`.
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
    // Bounding box of the candidate's bar group (all visible series at idx).
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
    // Drive the next frame — chart.draw() re-enters this hook, so the loop
    // self-sustains while a bar is in flight (rAF caps it to the refresh rate).
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

// `afterLayout`, NOT `afterDraw`: (1) `chartArea` and `scales` are final here;
// (2) it fires on every update AND on every resize (chart.js's resize path runs
// an update, which re-lays-out); (3) `inFlightPulse` re-enters `chart.draw()` at
// ~60fps via rAF while a candidate scores, so an afterDraw publisher would
// rebuild this array 60×/s for nothing.
const xBridgePlugin: Plugin<"bar", { onGeometry: (g: PlotGeometry) => void }> = {
  id: "xBridge",
  afterLayout(chart, _args, opts) {
    const emit = opts?.onGeometry;
    const x = chart.scales.x;
    if (!emit || !x) return;
    const { left, right, width } = chart.chartArea;
    // Hidden / zero-width card (a collapsed chat dropdown) makes every fraction
    // garbage. Keep the last good geometry; re-showing fires a resize, which
    // republishes.
    if (!(width > 0)) return;
    const n = chart.data.labels?.length ?? 0;
    const centers: number[] = [];
    for (let i = 0; i < n; i++) centers.push((x.getPixelForValue(i) - left) / width);
    emit({ left, rightGutter: chart.width - right, centers });
  },
};

// Stable identity — passing a fresh array each render churns the chart.
const CHART_PLUGINS = [
  barCapsPlugin,
  divergenceLinePlugin,
  inFlightPulsePlugin,
  meanFitnessCiWhiskerPlugin,
  xBridgePlugin,
];

// Above this real-bar count, x-axis labels rotate 60° so they stop
// overlapping their neighbours. Picked empirically against the card
// height (rotated labels eat ~30px of plot height).
const ROTATE_THRESHOLD = 8;

interface Props {
  views: CandidateView[];
  // The card's metric axis — one bar series per selected metric. Never empty.
  metrics: ReadonlySet<HeadlineMetric>;
  showWhatIf: boolean;
  // Draw the dashed cache-provenance line at each candidate's replayed share.
  showCache: boolean;
  // Draw the adopted line's reading on the cells all of it answered.
  showTrajectory: boolean;
  selectedKey: string | null;
  onSelect: (view: CandidateView | null) => void;
  // Bar index where the active mask first diverges from the realized record —
  // the red vertical divider is drawn at its left edge. null = no mask / no
  // divergence (no divider).
  divergenceBoundary: number | null;
  // Bar index of the candidate currently accumulating samples — it pulses
  // ("blinking") while live. null = nothing scoring (no pulse).
  inFlightIndex: number | null;
  // Publishes the plot geometry the dendrogram strip aligns to. MUST be a stable
  // callback: it rides the `options` memo, so a fresh identity per render would
  // force a chart.update() on every poll tick.
  onGeometry: (g: PlotGeometry) => void;
  unit: MeasuredUnit;
  // The metric this campaign's ENGINE elects on (served). It decides which bar reads at
  // full accent — so the loud bar is the DECIDING bar, not merely the familiar one.
  electedMetric: HeadlineMetric;
}

export const FitnessChart = memo(function FitnessChart({
  views,
  metrics,
  showWhatIf,
  showCache,
  showTrajectory,
  selectedKey,
  onSelect,
  divergenceBoundary,
  inFlightIndex,
  onGeometry,
  unit,
  electedMetric,
}: Props) {
  // Subscribe to theme so a flip re-runs this component and the data/options
  // memos below pick up the new getCss() values.
  const themeVersion = useThemeVersion();
  // One x-axis label per real bar. No empty-slot padding: chart.js spaces
  // categories evenly across the frame, and the `maxBarThickness` cap below
  // keeps individual bars narrow even at very low counts (a 2-bar round
  // shows two 28px bars centered, not two stretched-to-fill bars).
  const labels = useMemo(() => views.map((v) => v.label), [views]);

  const ctx = useMemo<SeriesCtx>(
    () => ({ metrics, showWhatIf, showCache, showTrajectory, views, unit, electedMetric }),
    [metrics, showWhatIf, showCache, showTrajectory, views, unit, electedMetric],
  );
  const active = useMemo(() => activeSeries(ctx), [ctx]);
  const showAbility = metrics.has("ability");

  // Per-bar border overlay — picks out the bar matching the shared
  // SelectionContext. Driven by --color-selection (app/styles/foundation/themes.css)
  // so it matches the dendrogram node's selected colour directly beneath it.
  const selectionBorder = useMemo(() => {
    if (selectedKey == null) return null;
    const idx = views.findIndex((v) => v.key === selectedKey);
    if (idx < 0) return null;
    const colour = getCss("--color-selection");
    return { idx, colour };
    // themeVersion gates getCss() — needed in deps; lint flags it unused.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [views, selectedKey, themeVersion]);

  // The INCUMBENT — the last bar on the spine wearing a crown. Not `findIndex`: this chart
  // plots a whole timeline, so every advancing round has a winner and the first one is C0.
  // The per-round crowns stay legible as filled dots on the dendrogram directly beneath; up
  // here one mark answers "who holds the title now", which is the question the card is for.
  // Null on a view with nothing crowned — a held round, a course, a round still scoring.
  const incumbentIdx = useMemo(() => {
    for (let i = views.length - 1; i >= 0; i--) if (views[i]?.is_winner) return i;
    return null;
  }, [views]);

  // "…and by how much" — the SERVED blocked lift over the floor the promotion gate judged this
  // candidate against. Claimed ONLY where the 95% interval excludes 0: an interval spanning it
  // means the round could not separate the winner from its parent, and a bare point estimate
  // there would report a win the measurement does not support. The number is in the tooltip
  // either way; the crown alone is the honest caption when the round cannot say.
  const crown = useMemo(() => {
    const v = incumbentIdx == null ? undefined : views[incumbentIdx];
    const { matchedParentLift: lift, matchedParentLiftCiLo: lo, matchedParentLiftCiHi: hi } =
      v ?? {};
    if (lift == null || lo == null || hi == null || (lo <= 0 && hi >= 0)) return "";
    return ` ${fmtSigned(lift, 2)}`;
  }, [views, incumbentIdx]);

  const data = useMemo<ChartData<"bar" | "line">>(() => {
    // BAR series only — the cache line sits on top of the group rather than inside it, so
    // counting it would narrow every bar the moment the overlay appeared.
    const bars = active.filter((s) => s.kind === "bar").length;
    const cat = bars <= 1 ? 0.55 : bars === 2 ? 0.75 : bars === 3 ? 0.9 : 0.95;
    // Dynamic bar thickness ceiling: chart frame fans out to ~720px on
    // wide layouts, so 25 bars × 3 series should leave room without
    // clipping. Scale max thickness down as the bar count grows; min 6.
    const barCount = Math.max(1, labels.length);
    const maxBar = Math.max(6, Math.min(28, Math.round(640 / (barCount * Math.max(1, bars)))));
    // Per-bar outline. A hollow series keeps its own edge; the selected bar overrides it (it
    // must match the dendrogram dot beneath). The incumbent deliberately gets none: on the
    // elected series its fill already IS the accent, so a ring would be invisible and any
    // other colour would compete with the selection stroke and the in-flight pulse. Its
    // marks — the crown and the lit x-axis label — sit off the bar, where contrast is free.
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
            // Bridging a gap claims a share for a candidate that has none.
            spanGaps: false,
            yAxisID: spec.axis,
            // ON TOP of the bars, and the sign is counterintuitive: chart.js sorts metasets
            // ASCENDING by `order` and then draws them in REVERSE (`Chart#_drawDatasets`), so
            // the LOWEST order paints last. Raising this to +1 hides the line behind the bars.
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
          // A signed series gets no minimum length: chart.js applies it as an absolute
          // length, so a negative logit's stub would land on the wrong side of zero.
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
    // Animation off — see chart-config.ts for the rationale. Chart.js's
    // internal data-diff is fast enough that a poll-driven update lands in
    // a single frame; the prior 200 ms tween was visible jank, not polish.
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
      // The incumbent's own label lights up — the second half of its mark, and the half that
      // works: it sits in the axis gutter, so unlike a ring on a filled bar it has nothing to
      // lose contrast against, at any bar count and in either theme.
      x: { grid: { display: false }, ticks: { color: (t) => getCss(t.index === incumbentIdx ? "--color-accent" : "--color-text-secondary"), font: (t) => ({ size: rotate ? 10 : 11, family: getCss("--font-mono"), weight: t.index === incumbentIdx ? "bold" as const : "normal" as const }), autoSkip: false, maxRotation: rotate ? 60 : 0, minRotation: rotate ? 60 : 0 } },
      y: { min: 0, max: 1, grid: { color: getCss("--color-border") }, ticks: { font: { size: 11 }, stepSize: 0.2 } },
      // θ's own axis, declared only while the ability series shows — otherwise the
      // right-hand gutter (which every dendrogram fraction is measured against)
      // would reserve space for an axis with no data.
      ...(showAbility
        ? {
            y1: {
              position: "right" as const,
              grid: { display: false },
              ticks: { font: { size: 11 } },
              // Labels the right axis as θ, sitting at the top next to the left axis's
              // "1" — replaces the "(right axis)" caption the legend used to carry.
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
            // Silent at 0 — the normal case, and noise on every bar.
            const cached = views[idx]?.cached_samples;
            if (cached != null && cached > 0 && n != null) {
              lines.push(`${cached} of ${unitCount(n, unit)} from cache`);
            }
            // Difficulty-adjusted ability — the metric the winner is elected on, so a
            // shorter (lower-accuracy) winner bar reads as "won on harder rows".
            const theta = views[idx]?.theta;
            if (typeof theta === "number") {
              const se = views[idx]?.theta_se;
              const tail = typeof se === "number" ? ` ± ${se.toFixed(2)}` : "";
              lines.push(`ability θ ${theta.toFixed(2)}${tail} (elected on θ, not accuracy)`);
            }
            const ciLo = views[idx]?.meanFitnessCiLo;
            const ciHi = views[idx]?.meanFitnessCiHi;
            if (typeof ciLo === "number" && typeof ciHi === "number") {
              lines.push(`95% CI [${ciLo.toFixed(3)}, ${ciHi.toFixed(3)}]`);
            }
            // The gate's own verdict. Says "could not separate" out loud rather than leaving
            // an interval that spans 0 to be read as a win.
            const lift = views[idx]?.matchedParentLift;
            const lLo = views[idx]?.matchedParentLiftCiLo;
            const lHi = views[idx]?.matchedParentLiftCiHi;
            if (lift != null && lLo != null && lHi != null) {
              const flat = lLo <= 0 && lHi >= 0 ? " — could not separate from its parent" : "";
              lines.push(
                `lift vs parent ${fmtSigned(lift)} [${fmtSigned(lLo)}, ${fmtSigned(lHi)}]${flat}`,
              );
            }
            // Say why there is no crown and no θ, rather than leaving the absence to be
            // read as a loss: an arm in a round nothing has won yet has nothing to have
            // lost to. Keyed on the ELECTION, not on the round close — those are two
            // moments, and after the first one the absence really does mean "lost".
            if (views[idx]?.electionPending) {
              lines.push("no election yet — nothing crowned in this round");
            }
            return lines.join("\n");
          },
        },
      },
      barCaps: { counts: partialPanels(views), incumbent: incumbentIdx, crown },
      divergenceLine: { index: divergenceBoundary },
      inFlightPulse: { index: inFlightIndex },
      meanFitnessCiWhisker: {
        anchor: whiskerAnchor(ctx),
        ciLo: views.map((v) => v.meanFitnessCiLo),
        ciHi: views.map((v) => v.meanFitnessCiHi),
      },
      xBridge: { onGeometry },
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [themeVersion, ctx, rotate, views, selectedKey, onSelect, divergenceBoundary, inFlightIndex, showAbility, incumbentIdx, crown, onGeometry]);

  return (
    <div className="fitness-chart-frame">
      {/* Explicit type argument — `type="bar"` alone infers the bar-only generic and
          rejects the cache overlay's line dataset. */}
      <Chart<"bar" | "line">
        type="bar"
        data={data}
        options={options}
        plugins={CHART_PLUGINS}
      />
    </div>
  );
});
