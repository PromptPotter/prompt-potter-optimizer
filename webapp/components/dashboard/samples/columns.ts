// Column model for the hard-samples roster table: the column definitions,
// the persisted-layout shape, and the pure cell-value / auto-width logic.
// No React — HardSamplesTable owns the rendering and the interaction state.

import { type CSSProperties } from "react";
import { type DatasetItem } from "@/lib/api";

export const STORAGE_KEY = "hs-grid:v1";
export const FOLDED_WIDTH = 28;
export const MIN_WIDTH = 32;

// Auto-sizing constants. Monospace 12px ≈ 7.4 px/char in practice. Cap any
// single column at MAX_AUTO_CH so a 2 kB query doesn't blow the layout to a
// thousand pixels — wrap or popover handles overflow instead.
const CHAR_PX = 7.4;
const CELL_PADDING_PX = 22;
const HEADER_PADDING_CH = 4;
const MAX_AUTO_CH = 50;

export interface MeasurementDot {
  hit: boolean;
  // Composite lex-sortable key. Equal ``ord`` values across rows share
  // a roster column so the Meas heat-map aligns vertically.
  ord: string;
}

export type ColId =
  | "rank"
  | "sample_id"
  | "measurements"
  | "hit_rate"
  | "pick_score"
  | "p_hat"
  | "delta"
  | "delta_se"
  | "n_obs"
  | "task"
  | "query"
  | "ground_truth";

export interface ColDef {
  id: ColId;
  label: string;
  align: "left" | "right" | "center";
  numeric: boolean;
}

// Order is the visual left-to-right column order. This is the aggregate
// per-sample roster — every column is a sample attribute or a cross-trial
// statistic. Per-evaluation fields (prediction, hit, latency, tokens …)
// belong to a single trial, not an aggregate, so they are not columns here.
// Sortable columns receive `numeric: true` for right-align + tabular-nums.
export const COLUMNS: ColDef[] = [
  { id: "rank",          label: "#",          align: "right",  numeric: true  },
  { id: "sample_id",     label: "ID",         align: "right",  numeric: true  },
  { id: "measurements",  label: "History",    align: "left",   numeric: true  },
  { id: "hit_rate",      label: "Hit rate",   align: "right",  numeric: true  },
  { id: "pick_score",    label: "Info gain",  align: "right",  numeric: true  },
  // Diagnostic columns — what feeds the Info gain. Promoted from the
  // Info-gain tooltip so the operator can read the queue mechanism's inputs
  // row-by-row without hovering. p_hat is the smoking gun for "always-miss
  // sample ranking high": EB-shrunk δ_s + tight se(δ_s) leave p_hat near
  // 0.5 even when hit rate is 0/N.
  { id: "p_hat",         label: "p̂",          align: "right",  numeric: true  },
  { id: "delta",         label: "δ",          align: "right",  numeric: true  },
  { id: "delta_se",      label: "se(δ)",      align: "right",  numeric: true  },
  { id: "n_obs",         label: "n",          align: "right",  numeric: true  },
  { id: "task",          label: "Task",       align: "left",   numeric: false },
  { id: "query",         label: "Input",      align: "left",   numeric: false },
  { id: "ground_truth",  label: "Output",     align: "left",   numeric: false },
];

export interface PersistedState {
  widths: Partial<Record<ColId, number>>;
  folded: ColId[];
  wrapped: ColId[];
  // When true ("Auto-sort"), the table ranks every row by Info gain
  // (pick_score) descending — the queue mechanism's expected decision-information-
  // gain — and header-click sorting is suppressed. The Info gain column
  // carries the sort marker. Default ON.
  syncLive: boolean;
  // When true ("Hide unmeasured"), rows with no measurements yet
  // (pick_score === null) are filtered out. Same behaviour under both
  // scopes — the operator can focus on what the optimizer has actually
  // touched. Default OFF.
  hideUnmeasured: boolean;
}

export const EMPTY_PERSISTED: PersistedState = {
  widths: {},
  folded: [],
  wrapped: [],
  syncLive: true,
  hideUnmeasured: false,
};

// Hit-rate → hue. 1 = cool green (always-hit), 0.5 = neutral grey
// (mixed), 0 = warm red (always-miss). Rows with zero measurements skip
// the style.
function hitRateStyle(hitRate: number): CSSProperties {
  const hue = 5 + hitRate * 125;
  const alpha = 0.18 + Math.abs(hitRate - 0.5) * 0.4;
  return { background: `hsla(${hue},70%,45%,${alpha.toFixed(3)})` };
}

export interface CellValue {
  text: string;
  raw: string | number | boolean | null;
  title?: string;
  style?: CSSProperties;
}

// Cell value for non-rank columns. Rank is computed inline in the render loop
// from the row's position in `sortedItems`.
export function cellFor(
  col: ColId,
  item: DatasetItem,
  meas: MeasurementDot[],
): CellValue {
  switch (col) {
    case "rank":
      return { text: "", raw: null };
    case "sample_id":
      return { text: String(item.sample_id), raw: item.sample_id };
    case "measurements":
      // Sort key is the measurement count; the heat-map canvas does the
      // visual rendering (see MeasHeatCell), not this text.
      return { text: meas.length > 0 ? String(meas.length) : "—", raw: meas.length };
    case "hit_rate": {
      // Empirical hit rate over every measurement of this sample —
      // ``hits/total``. The strongest bug-spotting signal: a 0/N row with
      // a large N is a sample the pipeline never solves (genuinely hard,
      // or a broken ground truth / scorer); a full N/N row is trivial.
      // Cell hue tracks the rate: green for always-hit, red for
      // always-miss, grey in between.
      const n = meas.length;
      if (n === 0) return { text: "—", raw: null };
      const hits = meas.reduce((k, m) => k + (m.hit ? 1 : 0), 0);
      const rate = hits / n;
      return {
        text: `${hits}/${n}`,
        raw: rate,
        title: `${hits} hit of ${n} measurements — ${(rate * 100).toFixed(0)}%`,
        style: hitRateStyle(rate),
      };
    }
    case "pick_score": {
      // Info gain — the queue mechanism's expected decision-information-gain from one
      // measurement of this sample. High = contested (the measurement tells
      // good prompts from bad); near-zero = always-hit or always-miss
      // (predictable, uninformative). The hover tooltip + the δ / se(δ) /
      // p̂ / n diagnostic columns explain why a given row ranks where it
      // ranks.
      if (item.pick_score === null) {
        return { text: "—", raw: null, title: "No measurements yet — info gain undefined." };
      }
      const dLine =
        item.delta !== null && item.delta_se !== null
          ? `δ ${item.delta >= 0 ? "+" : ""}${item.delta.toFixed(2)} ± ${item.delta_se.toFixed(2)}  (Rasch difficulty ± uncertainty)`
          : "δ —  (unmeasured)";
      const pLine =
        item.p_hat !== null
          ? `p̂(hit | seed)=${(item.p_hat * 100).toFixed(0)}%  (marginal hit prob the queue mechanism reads)`
          : "p̂ —  (unmeasured)";
      return {
        text: item.pick_score.toFixed(4),
        raw: item.pick_score,
        title:
          `Info gain ${item.pick_score.toFixed(6)} nats\n` +
          `${dLine}\n` +
          `${pLine}\n` +
          `${item.n_obs} tries\n` +
          `High = contested, the measurement separates good prompts from bad. ` +
          `Near-zero = always-hit or always-miss — predictable, uninformative.`,
      };
    }
    case "p_hat": {
      // Marginal hit probability at the seed's ability — what
      // decision-IG actually reads. 0.5 = contested; 0 or 1 = predictable.
      // The queue mechanism rewards contested + tight se(δ) — a 0/17 row whose
      // EB-shrunk δ keeps p̂ near 0.5 can outrank an honestly contested 1/2
      // row, which is the explanation operators want when reading the sort.
      if (item.p_hat === null) return { text: "—", raw: null };
      return {
        text: `${(item.p_hat * 100).toFixed(0)}%`,
        raw: item.p_hat,
        title:
          `p̂(hit | seed) = ${(item.p_hat * 100).toFixed(1)}%\n` +
          `σ((θ_seed − δ_s) / √(1 + π·(σ_θ² + se(δ)²)/8))\n` +
          `0.5 → contested at the seed's ability (high info gain potential).\n` +
          `0 or 1 → predictable; even a hit/miss tells the queue mechanism little.`,
      };
    }
    case "delta": {
      // Rasch difficulty δ_s — higher = harder for an average candidate.
      // EB-shrunk toward the population mean μ_δ, so extreme samples don't
      // peg infinity even at 0/N or N/N.
      if (item.delta === null) return { text: "—", raw: null };
      const sign = item.delta >= 0 ? "+" : "";
      return {
        text: `${sign}${item.delta.toFixed(2)}`,
        raw: item.delta,
        title:
          `Rasch difficulty δ_s = ${item.delta.toFixed(4)}\n` +
          `Empirical-Bayes shrinks δ toward μ_δ, so 0/N rows don't go to +∞.`,
      };
    }
    case "delta_se": {
      // SE of δ_s — how confidently the Rasch model places this sample's
      // difficulty. Small even at low n on boundary samples (prior precision
      // dominates observed Fisher when p(1−p) → 0); that's how a 0/17 row
      // ends up with tight se(δ) and the queue mechanism treating it as a
      // high-confidence, near-boundary measurement.
      if (item.delta_se === null) return { text: "—", raw: null };
      return {
        text: item.delta_se.toFixed(2),
        raw: item.delta_se,
        title:
          `se(δ_s) = ${item.delta_se.toFixed(4)}\n` +
          `Small se(δ) on a low-hit-rate row = EB tightness from prior precision, ` +
          `not data confidence in extremity. Queue mechanism reads it as "well-characterized" ` +
          `and rewards it.`,
      };
    }
    case "n_obs": {
      // Measurements feeding the Rasch fit. Disambiguates the EB
      // tight-se(δ) artefact — a tight se(δ) at n=2 is the prior talking,
      // not the data.
      return {
        text: String(item.n_obs),
        raw: item.n_obs,
        title: `${item.n_obs} measurements feeding the Rasch fit for this sample.`,
      };
    }
    case "task": {
      const t = item.task ?? "";
      return { text: t || "—", raw: t, title: t || undefined };
    }
    case "query":
      return { text: item.query, raw: item.query, title: item.query };
    case "ground_truth":
      return { text: item.ground_truth, raw: item.ground_truth, title: item.ground_truth };
  }
}

// Auto-size a column by sampling its rendered text. Floor is the header label
// length (so headers never get clipped); ceiling is MAX_AUTO_CH (long text
// wraps or expands via popover instead of forcing a 1000 px column).
export function autoWidthFor(
  col: ColDef,
  items: DatasetItem[],
  perSample: Map<number, MeasurementDot[]> | undefined,
  ordColsCount: number,
): number {
  const headerCh = col.label.length + HEADER_PADDING_CH;
  let maxCh = headerCh;
  if (col.id === "rank") {
    // Rank shows row position; longest is items.length digits.
    maxCh = Math.max(headerCh, String(Math.max(1, items.length)).length + 1);
  } else if (col.id === "measurements") {
    // History heat-map: one canvas per row. Auto-width only sets the
    // initial column size — 8 px per ordinal up to a 280 px ceiling so the
    // column never dominates the layout when ``ordCols`` runs into the
    // thousands. Floor of 96 px keeps the "History" header from clipping
    // (label + fold/resize tools). Operator-resizable from there; the
    // canvas compresses the full history into whatever width it has.
    return Math.max(96, Math.min(12 + ordColsCount * 8, 280));
  } else {
    for (const item of items) {
      const text = cellFor(col.id, item, perSample?.get(item.sample_id) ?? []).text;
      const ch = Math.min(text.length, MAX_AUTO_CH);
      if (ch > maxCh) maxCh = ch;
    }
  }
  return Math.max(MIN_WIDTH, Math.round(maxCh * CHAR_PX + CELL_PADDING_PX));
}

export const RANK_HINT = "Position in current sort order — click to clear sort";
