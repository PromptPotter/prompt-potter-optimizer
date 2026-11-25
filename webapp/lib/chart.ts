import {
  Chart as ChartJS,
  BarElement,
  LineElement,
  PointElement,
  CategoryScale,
  LinearScale,
  Filler,
  BarController,
  LineController,
  type Scale,
  type Tick,
} from "chart.js";
import { getCss, subscribeTheme } from "@/lib/theme";

// Chart.js v4 requires explicit registration of the components used. Never its tooltip plugin: a
// canvas paints no detail over its own marks — what a tap reads opens in the DOM below the chart.
let registered = false;

export function ensureChartRegistered(): void {
  if (registered) return;
  ChartJS.register(
    BarElement,
    LineElement,
    PointElement,
    CategoryScale,
    LinearScale,
    Filler,
    BarController,
    LineController,
  );
  // Before any chart mounts: the defaults ink the axes now, and a theme change re-inks them ahead of
  // a chart's re-render.
  applyChartDefaults();
  subscribeTheme(applyChartDefaults);
  // A column is the hit, not its drawn mark: a tap on a 6px bar at N=40 misses.
  ChartJS.defaults.interaction.mode = "index";
  ChartJS.defaults.interaction.intersect = false;
  registered = true;
}

// `animation: false`: a tween would re-animate every bar on each poll.
export const CHART_DEFAULTS = { responsive: true, maintainAspectRatio: false, animation: false } as const;

export function lineLook(ink: string, dashed = false) {
  return {
    borderColor: ink,
    backgroundColor: ink,
    borderWidth: 1.5,
    tension: 0.3,
    pointRadius: 2,
    ...(dashed ? { borderDash: [4, 3] } : {}),
  };
}

export function lineMotion(reducedMotion: boolean) {
  return reducedMotion ? (false as const) : { duration: 1000, easing: "easeOutQuart" as const };
}

// A label a canvas may draw: its box, centred on (x, y), and its rank: lower draws first.
export interface LabelBox {
  x: number;
  y: number;
  w: number;
  h: number;
  rank: number;
}

// Which labels to draw so no two touch: by rank, then left to right, each kept unless it comes
// within `gap` of one already kept. A label ranked Infinity is never drawn.
export function unclutteredLabels(boxes: readonly LabelBox[], gap: number): boolean[] {
  const kept: LabelBox[] = [];
  const shown = boxes.map(() => false);
  const order = boxes
    .map((b, i) => ({ b, i }))
    .filter(({ b }) => Number.isFinite(b.rank))
    .sort((p, q) => p.b.rank - q.b.rank || p.b.x - q.b.x);
  for (const { b, i } of order) {
    const clear = kept.every(
      (k) =>
        2 * Math.abs(k.x - b.x) >= k.w + b.w + 2 * gap ||
        2 * Math.abs(k.y - b.y) >= k.h + b.h + 2 * gap,
    );
    if (!clear) continue;
    kept.push(b);
    shown[i] = true;
  }
  return shown;
}

// The smallest step of 1, 2, 5, 10, 20, 50… at which the labels of every step-th ordinal clear their
// neighbours by `gap`. Labels come left to right, `x` the centre and `w` the width.
export function labelStep(labels: readonly { x: number; w: number; ordinal: number }[], gap: number): number {
  const top = Math.max(0, ...labels.map((l) => l.ordinal));
  const clearAt = (step: number): boolean => {
    let prev: { x: number; w: number } | null = null;
    for (const l of labels) {
      if (l.ordinal % step !== 0) continue;
      if (prev && l.x - prev.x < (l.w + prev.w) / 2 + gap) return false;
      prev = l;
    }
    return true;
  };
  for (let p = 1; ; p *= 10) {
    for (const m of [1, 2, 5]) {
      const step = m * p;
      if (step > top || clearAt(step)) return step;
    }
  }
}

// From a category x-axis's `afterTickToLabelConversion`: keeps the labels of every `labelStep`-th
// ordinal, one ordinal per tick, and blanks the rest. Blank, never null: a null label drops its
// tick and shifts every index after it.
export function thinTickLabels(scale: Scale, ordinals: readonly number[], font: string, gap: number): void {
  const { ticks, ctx } = scale;
  if (ticks.length === 0 || !(scale.width > 0)) return;
  const slot = scale.width / ticks.length;
  ctx.save();
  ctx.font = font;
  const labelled: { x: number; w: number; ordinal: number; tick: Tick }[] = [];
  ordinals.forEach((ordinal, i) => {
    const tick = ticks[i];
    if (tick?.label) labelled.push({ x: (i + 0.5) * slot, w: ctx.measureText(String(tick.label)).width, ordinal, tick });
  });
  ctx.restore();
  const step = labelStep(labelled, gap);
  for (const l of labelled) if (l.ordinal % step !== 0) l.tick.label = "";
}

export function applyChartDefaults(): void {
  ChartJS.defaults.color = getCss("--color-text-secondary");
  ChartJS.defaults.borderColor = getCss("--color-border");
}
