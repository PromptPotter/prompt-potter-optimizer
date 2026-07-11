// Pure geometry + colour helpers for the per-row measurement heat-map canvas
// (MeasHeatCell). No React, no DOM — unit-testable in isolation.

// Discrete cells never grow past this; beyond it the strip wastes space.
const MAX_CELL_PX = 8;

// One measurement: an ordinal slot and whether the sample hit it.
export interface HeatDot {
  ord: string;
  hit: boolean;
}

interface HeatLayout {
  // discrete: one output column per ordinal, left-packed at `cellW` (<= MAX).
  // bucket:   one output column per pixel, each folding several ordinals.
  mode: "discrete" | "bucket";
  len: number; // ordCols.length
  widthPx: number; // canvas CSS width
  count: number; // number of output columns drawn
  cellW: number; // CSS px per output column
}

// Pick discrete vs bucket from the ordinal count and the column width.
export function heatLayout(len: number, widthPx: number): HeatLayout {
  const w = Math.max(0, widthPx);
  if (len <= 0 || w <= 0) {
    return { mode: "discrete", len, widthPx: w, count: 0, cellW: 0 };
  }
  if (len <= w) {
    return {
      mode: "discrete",
      len,
      widthPx: w,
      count: len,
      cellW: Math.min(MAX_CELL_PX, w / len),
    };
  }
  const count = Math.floor(w);
  return { mode: "bucket", len, widthPx: w, count, cellW: w / count };
}

interface RowBucket {
  hits: number;
  total: number;
}

// Fold one row's measurements into output columns. Iterates the row's own
// (small) measurement set — never the full ordinal universe — so a redraw is
// O(measurements), not O(ordCols × rows).
export function bucketRow(
  layout: HeatLayout,
  byOrd: Map<string, HeatDot>,
  ordIndex: Map<string, number>,
): (RowBucket | null)[] {
  const out: (RowBucket | null)[] = new Array(layout.count).fill(null);
  if (layout.count === 0) return out;
  for (const [ord, dot] of byOrd) {
    const idx = ordIndex.get(ord);
    if (idx === undefined) continue;
    const col =
      layout.mode === "discrete"
        ? idx
        : Math.min(layout.count - 1, Math.floor((idx / layout.len) * layout.count));
    let b = out[col];
    if (!b) {
      b = { hits: 0, total: 0 };
      out[col] = b;
    }
    b.total += 1;
    if (dot.hit) b.hits += 1;
  }
  return out;
}

// Pixel span of an output column — snapped to integers so cells tile flush.
export function colX(layout: HeatLayout, col: number): { x: number; w: number } {
  const x = Math.round(col * layout.cellW);
  return { x, w: Math.max(1, Math.round((col + 1) * layout.cellW) - x) };
}

// Click x (CSS px) → ordinal index, or null when the click missed every cell.
export function xToOrdIndex(layout: HeatLayout, xCss: number): number | null {
  if (layout.len === 0 || layout.widthPx <= 0) return null;
  if (layout.mode === "discrete") {
    const i = Math.floor(xCss / layout.cellW);
    return i >= 0 && i < layout.len ? i : null;
  }
  const i = Math.floor((xCss / layout.widthPx) * layout.len);
  return Math.max(0, Math.min(layout.len - 1, i));
}

// Ordinal index → centre x (CSS px) — drives the selection marker.
export function ordIndexToXCss(layout: HeatLayout, i: number): number {
  if (layout.len === 0) return 0;
  if (layout.mode === "discrete") return (i + 0.5) * layout.cellW;
  return ((i + 0.5) / layout.len) * layout.widthPx;
}

export type Rgb = [number, number, number];

// "16,185,129" (the --color-*-rgb custom properties) → [16,185,129].
export function parseRgbTriple(s: string, fallback: Rgb): Rgb {
  const parts = s.split(",").map((p) => Number(p.trim()));
  if (parts.length !== 3) return fallback;
  const [r, g, b] = parts;
  if (r === undefined || g === undefined || b === undefined) return fallback;
  if (!Number.isFinite(r) || !Number.isFinite(g) || !Number.isFinite(b)) return fallback;
  return [r, g, b];
}

// Hit ratio → colour: 0 = miss (danger), 1 = hit (success), blended between.
export function mixHitColor(ratio: number, success: Rgb, danger: Rgb): string {
  const t = Math.max(0, Math.min(1, ratio));
  const r = Math.round(danger[0] + (success[0] - danger[0]) * t);
  const g = Math.round(danger[1] + (success[1] - danger[1]) * t);
  const b = Math.round(danger[2] + (success[2] - danger[2]) * t);
  return `rgb(${r},${g},${b})`;
}
