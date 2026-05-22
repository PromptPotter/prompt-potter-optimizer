"use client";
import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type PointerEvent,
} from "react";
import { type DatasetItem, type HardSamplesScope } from "@/lib/api";
import { type DashboardSnapshot } from "@/lib/poll";
import { MeasHeatCell } from "./MeasHeatCell";
import { heatLayout, ordIndexToXCss } from "@/lib/heat-canvas";

const STORAGE_KEY = "hs-grid:v1";
const FOLDED_WIDTH = 28;
const MIN_WIDTH = 32;

// Auto-sizing constants. Monospace 12px ≈ 7.4 px/char in practice. Cap any
// single column at MAX_AUTO_CH so a 2 kB query doesn't blow the layout to a
// thousand pixels — wrap or popover handles overflow instead.
const CHAR_PX = 7.4;
const CELL_PADDING_PX = 22;
const HEADER_PADDING_CH = 4;
const MAX_AUTO_CH = 50;

interface MeasurementDot {
  hit: boolean;
  // Composite lex-sortable key. Equal ``ord`` values across rows share
  // a roster column so the Meas heat-map aligns vertically.
  ord: string;
}

interface Props {
  dash: DashboardSnapshot | null;
  // `status === "live"` — gates the row-scoring blink. When the optimizer
  // process dies, `current_sample_id` is stranded in dashboard.json; without
  // this gate the matched row would pulse forever. `isLive` goes false once
  // the freshness signal lapses, so the blink stops on its own.
  isLive: boolean;
  // Active theme key — forwarded to the heat-map canvas so a palette flip
  // repaints it (canvas resolves colours imperatively, not via CSS vars).
  themeKey: string;
  // Per-sample chronological measurement dots. When supplied, the table
  // shows a "measurements" column with one dot per measurement; when
  // omitted, the column is hidden.
  perSample?: Map<number, MeasurementDot[]>;
  // Compact = tiny default height (~3 rows visible). User can still drag
  // the resize handle to grow it. Off = standard preset height.
  compact?: boolean;
  datasetName: string | null;
  datasetItems: DatasetItem[];
  datasetMeasuredCount: number;
  datasetUnmeasuredCount: number;
  // Held-out test fold size from campaign.json — shown in the footer as a
  // held-out note; those samples are NOT rows in this table.
  datasetSplitTest: number | null;
  // Data scope toggle. Campaign = the campaign's pooled Rasch fit over
  // every cycle in it (campaigns/{campaign_id}/hard_samples.json). Dataset
  // = the cross-campaign archive Rasch over every campaign on this dataset.
  // Owner (DashboardPane) refetches the preview when scope changes.
  scope?: HardSamplesScope;
  onScopeChange?: (s: HardSamplesScope) => void;
}

type ColId =
  | "rank"
  | "sample_id"
  | "measurements"
  | "hit_rate"
  | "miss_prob"
  | "pick_score"
  | "task"
  | "query"
  | "ground_truth";

interface ColDef {
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
const COLUMNS: ColDef[] = [
  { id: "rank",          label: "#",          align: "right",  numeric: true  },
  { id: "sample_id",     label: "ID",         align: "right",  numeric: true  },
  { id: "measurements",  label: "History",    align: "left",   numeric: true  },
  { id: "hit_rate",      label: "Hit rate",   align: "right",  numeric: true  },
  { id: "miss_prob",     label: "P(miss)",    align: "right",  numeric: true  },
  { id: "pick_score",    label: "Info gain",  align: "right",  numeric: true  },
  { id: "task",          label: "Task",       align: "left",   numeric: false },
  { id: "query",         label: "Input",      align: "left",   numeric: false },
  { id: "ground_truth",  label: "Output",     align: "left",   numeric: false },
];

interface PersistedState {
  widths: Partial<Record<ColId, number>>;
  folded: ColId[];
  wrapped: ColId[];
  // When true ("Auto-sort"), the table ranks every row by Info gain
  // (pick_score) descending — the picker's expected decision-information-
  // gain — and header-click sorting is suppressed. The Info gain column
  // carries the sort marker. Default ON.
  syncLive: boolean;
}

const EMPTY_PERSISTED: PersistedState = {
  widths: {},
  folded: [],
  wrapped: [],
  syncLive: true,
};

function loadPersisted(): PersistedState {
  if (typeof window === "undefined") return EMPTY_PERSISTED;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY_PERSISTED;
    const p = JSON.parse(raw) as Partial<PersistedState>;
    return {
      widths:  p.widths  ?? {},
      folded:  p.folded  ?? [],
      wrapped: p.wrapped ?? [],
      syncLive: p.syncLive ?? true,
    };
  } catch {
    return EMPTY_PERSISTED;
  }
}

function savePersisted(s: PersistedState): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch {
    /* localStorage may be unavailable (privacy mode); silently skip */
  }
}

// Miss-probability → hue. 0 = cool green (always-hit), 0.5 =
// neutral grey (no signal yet), 1 = warm red (always-miss).
function missProbStyle(s: number): CSSProperties {
  const clamped = Math.max(0, Math.min(1, s));
  const hue = 130 - clamped * 125;
  const alpha = 0.18 + Math.abs(clamped - 0.5) * 0.4;
  return { background: `hsla(${hue},70%,45%,${alpha.toFixed(3)})` };
}

interface CellValue {
  text: string;
  raw: string | number | boolean | null;
  title?: string;
  style?: CSSProperties;
}

// Cell value for non-rank columns. Rank is computed inline in the render loop
// from the row's position in `sortedItems`.
function cellFor(
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
      // Sits next to P(miss) so the observed rate and the Rasch estimate
      // can be eyeballed against each other.
      const n = meas.length;
      if (n === 0) return { text: "—", raw: null };
      const hits = meas.reduce((k, m) => k + (m.hit ? 1 : 0), 0);
      return {
        text: `${hits}/${n}`,
        raw: hits / n,
        title: `${hits} hit of ${n} measurements — ${((hits / n) * 100).toFixed(0)}%`,
      };
    }
    case "miss_prob":
      return {
        text: item.miss_prob.toFixed(2),
        raw: item.miss_prob,
        style: missProbStyle(item.miss_prob),
      };
    case "pick_score": {
      // Info gain — the picker's expected decision-information-gain from one
      // measurement of this sample. High = contested (the measurement tells
      // good prompts from bad); near-zero = always-hit or always-miss
      // (predictable, uninformative). The hover tooltip carries the Rasch
      // breakdown so the order can be debugged row by row.
      if (item.pick_score === null) {
        return { text: "—", raw: null, title: "No measurements yet — info gain undefined." };
      }
      const dLine =
        item.delta !== null && item.delta_se !== null
          ? `δ ${item.delta >= 0 ? "+" : ""}${item.delta.toFixed(2)} ± ${item.delta_se.toFixed(2)}  (Rasch difficulty ± uncertainty)`
          : "δ —  (unmeasured)";
      return {
        text: item.pick_score.toFixed(4),
        raw: item.pick_score,
        title:
          `Info gain ${item.pick_score.toFixed(6)} nats\n` +
          `${dLine}\n` +
          `P(miss) ${item.miss_prob.toFixed(2)}  ·  ${item.n_obs} tries\n` +
          `High = contested, the measurement separates good prompts from bad. ` +
          `Near-zero = always-hit or always-miss — predictable, uninformative.`,
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
function autoWidthFor(
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

const RANK_HINT = "Position in current sort order — click to clear sort";

export function HardSamplesTable({
  dash,
  isLive,
  themeKey,
  perSample,
  compact,
  datasetName,
  datasetItems,
  datasetMeasuredCount,
  datasetUnmeasuredCount,
  datasetSplitTest,
  scope,
  onScopeChange,
}: Props) {
  // Drop columns with nothing to show: the History heat-map needs
  // `perSample`; the Task column needs at least one sample carrying a task
  // label (datasets without task families never populate it).
  const columns = useMemo<ColDef[]>(() => {
    const hasTask = datasetItems.some((it) => (it.task ?? "") !== "");
    return COLUMNS.filter(
      (c) =>
        (perSample || c.id !== "measurements") && (hasTask || c.id !== "task"),
    );
  }, [perSample, datasetItems]);
  const items = datasetItems;
  const measuredCount = datasetMeasuredCount;
  const unmeasuredCount = datasetUnmeasuredCount;

  const [persisted, setPersisted] = useState<PersistedState>(() => loadPersisted());
  const [sortBy, setSortBy] = useState<{ col: ColId; dir: "asc" | "desc" } | null>(null);
  const [popover, setPopover] = useState<{ col: ColId; sampleId: number; text: string } | null>(null);
  // Currently-highlighted Meas roster column. Click a cell → its ``ord``
  // lights up across every row; arrows pan left/right; Escape clears.
  const [selectedOrd, setSelectedOrd] = useState<string | null>(null);
  // Heat-map hover read-out — one shared tooltip, not a title per cell.
  const [hoverTip, setHoverTip] = useState<
    { ord: string; hit: boolean | null; x: number; y: number } | null
  >(null);

  useEffect(() => savePersisted(persisted), [persisted]);

  // Dismiss popover on Escape.
  useEffect(() => {
    if (!popover) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPopover(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [popover]);

  // Stable signature of measurement counts per sample. Drives auto-width
  // recompute for the History column on real changes only.
  const perSampleSig = useMemo(() => {
    if (!perSample) return "";
    return [...perSample.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([sid, ms]) => `${sid}:${ms.length}`)
      .join(",");
  }, [perSample]);

  // Content signature of every measurement (ord + hit), sample-sorted.
  // Stable across polls — `perSample` is a fresh Map each poll but its
  // contents rarely change — so it is the sole gate for canvas repaints.
  const drawSig = useMemo(() => {
    if (!perSample) return "";
    return [...perSample.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(
        ([sid, ms]) =>
          `${sid}:${ms.map((m) => m.ord + (m.hit ? "1" : "0")).join("")}`,
      )
      .join("|");
  }, [perSample]);

  // Global ordinal universe — the union of every ord present across rows,
  // sorted lex. Each ord becomes one column in the Meas roster so rows
  // missing a given measurement show a blank cell at the same X as rows
  // that have it. This is what turns the old left-packed strip into a
  // proper heat-map.
  const ordCols = useMemo(() => {
    if (!perSample) return [] as string[];
    const all = new Set<string>();
    for (const ms of perSample.values()) for (const m of ms) all.add(m.ord);
    return [...all].sort();
  }, [perSample]);

  // Reverse index ord → position, so a row folds its own measurements into
  // canvas columns without scanning the whole ordinal universe.
  const ordIndex = useMemo(() => {
    const m = new Map<string, number>();
    for (let i = 0; i < ordCols.length; i++) m.set(ordCols[i], i);
    return m;
  }, [ordCols]);

  // Arrow-key nav for the Meas column highlight. Active only while an
  // ord is selected. Escape clears; arrows pan with wrap-around so the
  // operator can step off either end without snagging.
  useEffect(() => {
    if (selectedOrd == null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSelectedOrd(null);
        return;
      }
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if (ordCols.length === 0) return;
      e.preventDefault();
      const i = ordCols.indexOf(selectedOrd);
      const step = e.key === "ArrowRight" ? 1 : -1;
      const next = i < 0 ? 0 : (i + step + ordCols.length) % ordCols.length;
      setSelectedOrd(ordCols[next]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedOrd, ordCols]);

  // Drop the selection silently when the underlying ord vanishes (scope
  // toggle, cycle change). Avoids a stale highlight pinned to nothing.
  useEffect(() => {
    if (selectedOrd != null && !ordCols.includes(selectedOrd)) {
      setSelectedOrd(null);
    }
  }, [selectedOrd, ordCols]);

  const autoWidths = useMemo<Partial<Record<ColId, number>>>(() => {
    if (items.length === 0) return {};
    const w: Partial<Record<ColId, number>> = {};
    for (const col of columns) {
      w[col.id] = autoWidthFor(col, items, perSample, ordCols.length);
    }
    return w;
    // `perSample` is read inside via cellFor; `perSampleSig` captures its
    // per-sample counts so widths recompute on real changes, not every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, columns, perSampleSig, ordCols.length]);

  // Live-sort ("Auto-sort"): rank the WHOLE table by Info gain (pick_score)
  // descending — the picker's expected decision-information-gain. Contested
  // samples rise to the top; always-hit and always-miss samples sink
  // together at the bottom. Unmeasured rows (null pick_score) go last in
  // sample_id order. This makes the Info gain column the literal sort key
  // across every row, not just the round's ~20-sample live subset.
  const liveSortActive = persisted.syncLive;
  const sortedItems = useMemo(() => {
    if (liveSortActive) {
      return [...items].sort((a, b) => {
        const pa = a.pick_score;
        const pb = b.pick_score;
        if (pa === null || pb === null) {
          if (pa === pb) return a.sample_id - b.sample_id;
          return pa === null ? 1 : -1;
        }
        if (pa !== pb) return pb - pa;
        return a.sample_id - b.sample_id;
      });
    }
    if (!sortBy) return items;
    const { col, dir } = sortBy;
    const sign = dir === "asc" ? 1 : -1;
    const keyOf = (it: DatasetItem): number | string => {
      const v = cellFor(col, it, perSample?.get(it.sample_id) ?? []).raw;
      if (v === null || v === undefined) return Number.NEGATIVE_INFINITY;
      if (typeof v === "boolean") return v ? 1 : 0;
      return v;
    };
    return [...items].sort((a, b) => {
      const ka = keyOf(a);
      const kb = keyOf(b);
      if (typeof ka === "number" && typeof kb === "number") return (ka - kb) * sign;
      return String(ka).localeCompare(String(kb)) * sign;
    });
  }, [items, sortBy, perSample, liveSortActive]);

  const widthFor = (col: ColDef): number => {
    if (persisted.folded.includes(col.id)) return FOLDED_WIDTH;
    return persisted.widths[col.id] ?? autoWidths[col.id] ?? 80;
  };

  // Meas column geometry. The per-row canvas fills this width; the
  // selection marker is anchored at the column's left edge inside the grid.
  const measCol = columns.find((c) => c.id === "measurements");
  const measColWidth = measCol ? widthFor(measCol) : 0;
  const measColFolded = persisted.folded.includes("measurements");
  let measColLeft = 0;
  for (const c of columns) {
    if (c.id === "measurements") break;
    measColLeft += widthFor(c);
  }

  const gridTemplate = columns.map((c) => `${widthFor(c)}px`).join(" ");
  const totalWidth = columns.reduce((sum, c) => sum + widthFor(c), 0);

  const startResize = (col: ColId) => (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startWidth = widthFor(columns.find((c) => c.id === col)!);
    const onMove = (ev: globalThis.PointerEvent) => {
      const dx = ev.clientX - startX;
      const w = Math.max(MIN_WIDTH, Math.round(startWidth + dx));
      setPersisted((p) => ({ ...p, widths: { ...p.widths, [col]: w } }));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const toggleFold = (col: ColId) => {
    setPersisted((p) => {
      const folded = p.folded.includes(col)
        ? p.folded.filter((c) => c !== col)
        : [...p.folded, col];
      return { ...p, folded };
    });
  };

  const toggleWrap = (col: ColId) => {
    setPersisted((p) => {
      const wrapped = p.wrapped.includes(col)
        ? p.wrapped.filter((c) => c !== col)
        : [...p.wrapped, col];
      return { ...p, wrapped };
    });
  };

  const cycleSort = (col: ColId) => {
    setSortBy((prev) => {
      if (!prev || prev.col !== col) return { col, dir: "asc" };
      if (prev.dir === "asc") return { col, dir: "desc" };
      return null;
    });
  };

  const handleHeaderClick = (col: ColId, folded: boolean) => {
    if (folded) {
      toggleFold(col);
      return;
    }
    if (liveSortActive) {
      // Live-sort owns the order — column-click sorting is a no-op until
      // the operator unlocks it via the sync chip. The header still
      // reacts to fold/wrap/resize controls; just the sort cycle is mute.
      return;
    }
    if (col === "rank") {
      // Rank header is a "clear sort" affordance — sorting by rank itself is
      // either a no-op (current order) or its inverse, neither useful.
      setSortBy(null);
      return;
    }
    cycleSort(col);
  };

  const resetLayout = () => {
    setPersisted(EMPTY_PERSISTED);
    setSortBy(null);
  };

  if (items.length === 0) return null;

  const total = measuredCount + unmeasuredCount;
  const tag = datasetName ? `${datasetName} · ` : "";
  // Text-wrap toggle applies to left-aligned text columns only — never the
  // History column (a canvas, nothing to wrap; dropping it also frees the
  // header room the "History" label needs).
  const wrappable = (col: ColDef): boolean =>
    col.align === "left" && col.id !== "measurements";

  return (
    <div className="hs-zone">
      <div
        className={`hs-block${compact ? " compact" : ""}`}
        // Width tracks the columns' total — folding/narrowing a column
        // shrinks the block. Set as a custom property, not `width`, so a
        // manual corner-resize (which writes inline `width`) still wins
        // and survives re-renders.
        style={{ "--hs-table-w": `${totalWidth}px` } as CSSProperties}
      >
        <div className="hs-scroll">
          <div
            className={`hs-grid${liveSortActive ? " sync-live" : ""}`}
            style={{
              gridTemplateColumns: gridTemplate,
              minWidth: `${totalWidth}px`,
            }}
          >
            {columns.map((col) => {
              const folded = persisted.folded.includes(col.id);
              // Under live-sort the rows follow the picker's blended
              // pick-value ranking — which is the "Pick" column descending;
              // mark it so the operator can see what the order is. Otherwise
              // the manual header-click sort owns the marker.
              const sorted = liveSortActive
                ? col.id === "pick_score"
                  ? "desc"
                  : null
                : sortBy?.col === col.id
                  ? sortBy.dir
                  : null;
              const isRank = col.id === "rank";
              const headerTitle = folded
                ? "Unfold column"
                : col.id === "pick_score"
                  ? "Info gain — expected decision-information-gain from one " +
                    "measurement. Auto-sort ranks every row by this, highest first."
                  : liveSortActive
                    ? "Auto-sort is ranking by Info gain — toggle it off to sort by this column."
                    : isRank
                      ? RANK_HINT
                      : `Sort by ${col.label}`;
              return (
                <div
                  key={col.id}
                  className={`hs-header${folded ? " folded" : ""}${sorted ? " sorted" : ""}${isRank ? " rank" : ""}${liveSortActive && !folded ? " sort-locked" : ""}`}
                  data-align={col.align}
                >
                  <button
                    type="button"
                    className="hs-header-label"
                    onClick={() => handleHeaderClick(col.id, folded)}
                    title={headerTitle}
                  >
                    {folded ? "⟩" : col.label}
                    {sorted && !folded && (
                      <span className="hs-sort-mark">{sorted === "asc" ? "▲" : "▼"}</span>
                    )}
                  </button>
                  {!folded && !isRank && (
                    <>
                      <div className="hs-header-tools">
                        {wrappable(col) && (
                          <button
                            type="button"
                            className={`hs-tool${persisted.wrapped.includes(col.id) ? " on" : ""}`}
                            onClick={() => toggleWrap(col.id)}
                            title={persisted.wrapped.includes(col.id) ? "Stop wrapping" : "Wrap text"}
                            aria-label="Toggle text wrap"
                          >
                            ↵
                          </button>
                        )}
                        <button
                          type="button"
                          className="hs-tool"
                          onClick={() => toggleFold(col.id)}
                          title="Fold column"
                          aria-label="Fold column"
                        >
                          ⟨
                        </button>
                      </div>
                      <div
                        className="hs-resize"
                        onPointerDown={startResize(col.id)}
                        title="Drag to resize"
                      />
                    </>
                  )}
                </div>
              );
            })}

            {sortedItems.map((item, idx) => {
              const meas = perSample?.get(item.sample_id) ?? [];
              // Per-row ord → dot lookup the heat-map canvas folds into
              // pixel columns (see MeasHeatCell / heat-canvas.ts).
              const byOrd = new Map<string, MeasurementDot>();
              for (const m of meas) byOrd.set(m.ord, m);
              // Mark every cell in the row currently being scored so the
              // soft-blink keyframe (globals.css) animates the whole row,
              // not just one cell. Independent of the sort-sync toggle —
              // operators sorting manually still want to see "this is the
              // row the loop is on right now". Gated on `isLive` + the
              // scoring phase so a stranded `current_sample_id` (process
              // killed mid-sample, or a non-scoring phase) never blinks.
              const isRunning =
                isLive &&
                dash?.state === "scoring" &&
                typeof dash?.current_sample_id === "number" &&
                item.sample_id === dash.current_sample_id;
              return columns.map((col) => {
                const folded = persisted.folded.includes(col.id);
                const wrapped = persisted.wrapped.includes(col.id) && wrappable(col);
                const isRank = col.id === "rank";
                const isMeas = col.id === "measurements";
                const cell = isRank
                  ? ({ text: String(idx + 1), raw: idx + 1 } as CellValue)
                  : cellFor(col.id, item, meas);
                const isExpandable =
                  !isRank &&
                  !isMeas &&
                  !col.numeric &&
                  col.align === "left" &&
                  Boolean(cell.raw);
                const onClick = folded
                  ? () => toggleFold(col.id)
                  : isExpandable
                    ? () =>
                        setPopover({
                          col: col.id,
                          sampleId: item.sample_id,
                          text: String(cell.raw),
                        })
                    : undefined;
                return (
                  <div
                    key={`${item.sample_id}-${col.id}`}
                    className={`hs-cell${folded ? " folded" : ""}${wrapped ? " wrapped" : ""}${isRank ? " rank" : ""}${isMeas ? " meas" : ""}`}
                    data-align={col.align}
                    data-running={isRunning ? "true" : undefined}
                    style={cell.style}
                    title={folded ? undefined : cell.title}
                    onClick={onClick}
                  >
                    {folded ? "" : isMeas ? (
                      ordCols.length === 0 && !isRunning ? (
                        <span className="hs-heat-empty">—</span>
                      ) : (
                        <MeasHeatCell
                          byOrd={byOrd}
                          ordCols={ordCols}
                          ordIndex={ordIndex}
                          widthPx={measColWidth}
                          isRunning={isRunning}
                          drawSig={drawSig}
                          themeKey={themeKey}
                          onSelectOrd={(ord) =>
                            setSelectedOrd((cur) => (cur === ord ? null : ord))
                          }
                          onHover={(ord, hit, x, y) =>
                            setHoverTip({ ord, hit, x, y })
                          }
                          onHoverEnd={() => setHoverTip(null)}
                        />
                      )
                    ) : (
                      cell.text
                    )}
                  </div>
                );
              });
            })}

            {selectedOrd != null &&
              !measColFolded &&
              (() => {
                const idx = ordIndex.get(selectedOrd);
                if (idx == null) return null;
                const layout = heatLayout(ordCols.length, measColWidth);
                const x = measColLeft + ordIndexToXCss(layout, idx);
                return (
                  <div
                    className="hs-heat-marker"
                    style={{ left: `${x - 1}px` }}
                    aria-hidden="true"
                  />
                );
              })()}
          </div>
        </div>
        <div className="hs-footer">
          {scope && onScopeChange ? (
            <div
              className="hs-scope-toggle"
              role="radiogroup"
              aria-label="Hard-sample data scope"
              title={
                scope === "campaign"
                  ? "Showing this campaign's pooled evidence. Toggle to every campaign on this dataset."
                  : "Showing every campaign on this dataset (cross-campaign archive). Toggle to this campaign only."
              }
            >
              <button
                type="button"
                role="radio"
                aria-checked={scope === "campaign"}
                className={`hs-scope-opt${scope === "campaign" ? " on" : ""}`}
                onClick={() => onScopeChange("campaign")}
              >
                This campaign
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={scope === "dataset"}
                className={`hs-scope-opt${scope === "dataset" ? " on" : ""}`}
                onClick={() => onScopeChange("dataset")}
              >
                All campaigns (dataset)
              </button>
            </div>
          ) : null}
          <label
            className="hs-sync-toggle"
            title={
              persisted.syncLive
                ? "Table mirrors the optimizer's live difficulty sort. Untick to sort columns manually."
                : "Sort with column headers. Tick to follow the optimizer's live difficulty sort."
            }
          >
            <input
              type="checkbox"
              checked={persisted.syncLive}
              onChange={() =>
                setPersisted((p) => ({ ...p, syncLive: !p.syncLive }))
              }
            />
            Auto-sort
          </label>
          <span
            className="hs-counts"
            title={
              datasetSplitTest != null
                ? `This table is the ${total}-sample training bank. A separate ` +
                  `${datasetSplitTest}-sample test fold is held out — not shown here.`
                : undefined
            }
          >
            {tag}Measured {measuredCount} · Unmeasured {unmeasuredCount} · Total{" "}
            {total}
            {datasetSplitTest != null ? ` · ${datasetSplitTest} test held out` : ""}
          </span>
          <button type="button" className="hs-reset" onClick={resetLayout} title="Reset column widths, folds, wraps, sort">
            Reset layout
          </button>
        </div>
      </div>

      {popover && (
        <div className="hs-popover-backdrop" onClick={() => setPopover(null)}>
          <div className="hs-popover" onClick={(e) => e.stopPropagation()}>
            <div className="hs-popover-header">
              <span>Sample {popover.sampleId} · {COLUMNS.find((c) => c.id === popover.col)?.label}</span>
              <button type="button" onClick={() => setPopover(null)} aria-label="Close">×</button>
            </div>
            <pre className="hs-popover-body">{popover.text}</pre>
          </div>
        </div>
      )}

      {hoverTip && (
        <div
          className="hs-heat-tip"
          style={{ left: `${hoverTip.x + 14}px`, top: `${hoverTip.y + 14}px` }}
        >
          {hoverTip.hit == null ? "—" : hoverTip.hit ? "HIT" : "MISS"} ·{" "}
          {hoverTip.ord}
        </div>
      )}
    </div>
  );
}
