"use client";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { type DatasetItem, type HardSamplesScope } from "@/lib/api";
import { type DashboardSnapshot } from "@/lib/poll";
import { MeasHeatCell } from "./MeasHeatCell";
import { heatLayout, ordIndexToXCss } from "@/lib/heat-canvas";
import { useLocalStorage } from "@/lib/useLocalStorage";
import {
  autoWidthFor,
  cellFor,
  COLUMNS,
  EMPTY_PERSISTED,
  FOLDED_WIDTH,
  MIN_WIDTH,
  RANK_HINT,
  STORAGE_KEY,
  type CellValue,
  type ColDef,
  type ColId,
  type MeasurementDot,
  type PersistedState,
} from "./hard-samples/columns";
import { HardSamplesFooter } from "./hard-samples/HardSamplesFooter";
import {
  HardSamplesHeatTip,
  HardSamplesPopover,
} from "./hard-samples/HardSamplesPopover";

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
  // Displayed roster is from a prior (unit, scope) and a fresh fetch is in
  // flight — the table dims via the `stale` data-attr but never blanks.
  datasetStale?: boolean;
}

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
  datasetStale,
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
  const measuredCount = datasetMeasuredCount;
  const unmeasuredCount = datasetUnmeasuredCount;

  const [persisted, setPersisted] = useLocalStorage<PersistedState>(
    STORAGE_KEY,
    EMPTY_PERSISTED,
    // Merge a stored blob over the defaults so a field added since it was
    // written is still present.
    { deserialize: (raw) => ({ ...EMPTY_PERSISTED, ...(JSON.parse(raw) as Partial<PersistedState>) }) },
  );
  const [sortBy, setSortBy] = useState<{ col: ColId; dir: "asc" | "desc" } | null>(null);
  const [popover, setPopover] = useState<{ col: ColId; sampleId: number; text: string } | null>(null);
  // Currently-highlighted Meas roster column. Click a cell → its ``ord``
  // lights up across every row; arrows pan left/right; Escape clears.
  const [selectedOrd, setSelectedOrd] = useState<string | null>(null);
  // Heat-map hover read-out — one shared tooltip, not a title per cell.
  const [hoverTip, setHoverTip] = useState<
    { ord: string; hit: boolean | null; x: number; y: number } | null
  >(null);

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

  // Drop the selection render-phase when its ord vanishes (scope toggle,
  // cycle change) — avoids a stale highlight pinned to nothing. Converges:
  // once null, the guard is false.
  if (selectedOrd != null && !ordCols.includes(selectedOrd)) {
    setSelectedOrd(null);
  }

  // Filter unmeasured rows out when the operator has ticked "Hide
  // unmeasured". The signal is the same one their eye is reading: does
  // the row have any dot in the History column. `pick_score` is the
  // WRONG signal — the Rasch model assigns a prior to every sample, so
  // `pick_score !== null` is true for rows with zero actual
  // measurements. The footer's API-sourced measured/unmeasured counts
  // mean something different (Rasch-fit population) and are unrelated.
  const items = useMemo(() => {
    if (!persisted.hideUnmeasured) return datasetItems;
    if (!perSample) return datasetItems;
    return datasetItems.filter(
      (it) => (perSample.get(it.sample_id)?.length ?? 0) > 0,
    );
  }, [datasetItems, perSample, persisted.hideUnmeasured]);

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
  // descending — the queue mechanism's expected decision-information-gain. Contested
  // samples rise to the top; always-hit and always-miss samples sink
  // together at the bottom. Rows without any actual measurement in this
  // scope sink last in sample_id order. Dot-presence is the
  // "measured-in-scope" signal — `pick_score !== null` is non-null on
  // prior-only rows too, so it overcounts under campaign scope.
  const liveSortActive = persisted.syncLive;
  // Numeric FNV-1a hash over the sort-affecting fields. Same role the old
  // string-concat signature played — sortedIds re-runs only when this
  // changes — but ~100× cheaper: no string allocation, just one O(n) walk
  // emitting a single uint32. pick_score is quantised to 1e-6 (the value's
  // visible precision in the picker) so float jitter doesn't churn the key.
  const liveSortSig = useMemo<number>(() => {
    if (!liveSortActive) return 0;
    let h = 2166136261;
    for (const it of items) {
      const m = (perSample?.get(it.sample_id)?.length ?? 0) > 0 ? 1 : 0;
      const pi = it.pick_score === null ? -1 : Math.round(it.pick_score * 1e6) | 0;
      h = Math.imul(h ^ it.sample_id, 16777619) >>> 0;
      h = Math.imul(h ^ m, 16777619) >>> 0;
      h = Math.imul(h ^ pi, 16777619) >>> 0;
    }
    return h;
  }, [items, perSample, liveSortActive]);
  // Manual-sort hash for when Auto-sort is off. Keys on the picked column's
  // raw value so a poll that only moved an unrelated field doesn't shuffle
  // the visible sort.
  const manualSortSig = useMemo<number>(() => {
    if (liveSortActive || !sortBy) return 0;
    let h = 2166136261;
    for (const it of items) {
      const v = cellFor(sortBy.col, it, perSample?.get(it.sample_id) ?? []).raw;
      let k: number;
      if (v === null || v === undefined) k = -1;
      else if (typeof v === "number") k = Math.round(v * 1e6) | 0;
      else if (typeof v === "boolean") k = v ? 1 : 0;
      else {
        // String hash mixed in. Lightweight — sortBy doesn't change every poll.
        let s = 5381;
        const str = String(v);
        for (let i = 0; i < str.length; i++) s = (s * 33) ^ str.charCodeAt(i);
        k = s | 0;
      }
      h = Math.imul(h ^ it.sample_id, 16777619) >>> 0;
      h = Math.imul(h ^ k, 16777619) >>> 0;
    }
    return h;
  }, [items, perSample, sortBy, liveSortActive]);
  // Sort the IDs, not the items. ID order is the actual stability surface;
  // the render loop projects items by ID downstream so cell values always
  // reflect the latest poll. Dependencies are the content signatures, so
  // unchanged-content polls return the prior reference.
  const sortedIds = useMemo<number[]>(() => {
    const measuredIn = (it: DatasetItem): boolean =>
      (perSample?.get(it.sample_id)?.length ?? 0) > 0;
    if (liveSortActive) {
      return [...items]
        .sort((a, b) => {
          const ma = measuredIn(a);
          const mb = measuredIn(b);
          if (ma !== mb) return ma ? -1 : 1;
          if (!ma) return a.sample_id - b.sample_id;
          const pa = a.pick_score ?? Number.NEGATIVE_INFINITY;
          const pb = b.pick_score ?? Number.NEGATIVE_INFINITY;
          if (pa !== pb) return pb - pa;
          return a.sample_id - b.sample_id;
        })
        .map((it) => it.sample_id);
    }
    if (!sortBy) return items.map((it) => it.sample_id);
    const { col, dir } = sortBy;
    const sign = dir === "asc" ? 1 : -1;
    const keyOf = (it: DatasetItem): number | string => {
      const v = cellFor(col, it, perSample?.get(it.sample_id) ?? []).raw;
      if (v === null || v === undefined) return Number.NEGATIVE_INFINITY;
      if (typeof v === "boolean") return v ? 1 : 0;
      return v;
    };
    return [...items]
      .sort((a, b) => {
        const ka = keyOf(a);
        const kb = keyOf(b);
        if (typeof ka === "number" && typeof kb === "number") return (ka - kb) * sign;
        return String(ka).localeCompare(String(kb)) * sign;
      })
      .map((it) => it.sample_id);
    // items / perSample / sortBy are captured into the closure via the
    // signatures above; redeclaring them as deps would re-run on every
    // poll and defeat the damping.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveSortActive, sortBy, liveSortSig, manualSortSig]);
  // Project items in the stable order. ``items`` may re-identify each
  // poll, so this fresh map keeps cell values current; row position
  // tracks ``sortedIds`` and stays stable.
  const sortedItems = useMemo(() => {
    const byId = new Map<number, DatasetItem>();
    for (const it of items) byId.set(it.sample_id, it);
    const out: DatasetItem[] = [];
    for (const sid of sortedIds) {
      const it = byId.get(sid);
      if (it) out.push(it);
    }
    return out;
  }, [items, sortedIds]);

  // Per-sample ord → dot lookup. Built once per `perSample` change rather
  // than once per row per render — the row loop just `.get(sample_id)` it.
  // Identity equality on the inner Maps is preserved across renders that
  // don't touch the data, so `MeasHeatCell`'s prop equality survives polls.
  const byOrdBySample = useMemo(() => {
    const out = new Map<number, Map<string, MeasurementDot>>();
    if (!perSample) return out;
    for (const [sid, ms] of perSample) {
      const m = new Map<string, MeasurementDot>();
      for (const x of ms) m.set(x.ord, x);
      out.set(sid, m);
    }
    return out;
  }, [perSample]);
  const EMPTY_BY_ORD = useMemo(() => new Map<string, MeasurementDot>(), []);

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

  // Virtualised body. The scroll element holds the sticky header + the
  // window of rows; tanstack-virtual mounts only the rows in (or near) the
  // viewport so DOM cost stays O(visible) instead of O(items). estimateSize
  // matches the cell's `contain-intrinsic-size` so the scroll-bar is
  // accurate before any row measures. Overscan 10 keeps a few rows above
  // and below the viewport pre-painted to absorb fast scrolls.
  const scrollRef = useRef<HTMLDivElement>(null);
  const ROW_HEIGHT = 26;
  const virtualizer = useVirtualizer({
    count: sortedItems.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
  });
  const virtualRows = virtualizer.getVirtualItems();
  const totalHeight = virtualizer.getTotalSize();

  if (datasetItems.length === 0) return null;

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
        data-stale={datasetStale ? "true" : undefined}
      >
        <div className="hs-scroll" ref={scrollRef}>
          {/* Header row — sits at the top of the scroll element; each
              .hs-header cell carries its own position:sticky;top:0 so it
              hugs the scroll container's top edge as rows pass under it. */}
          <div
            className={`hs-grid hs-header-row${liveSortActive ? " sync-live" : ""}`}
            style={{
              gridTemplateColumns: gridTemplate,
              minWidth: `${totalWidth}px`,
            }}
          >
            {columns.map((col) => {
              const folded = persisted.folded.includes(col.id);
              // Under live-sort the rows follow the queue mechanism's blended
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
          </div>

          {/* Virtualised body. Height is set so the scroll-bar reflects the
              full row count; rows are absolutely positioned via translateY.
              tanstack-virtual mounts only the rows in (or near) the
              viewport, so rendering 1000 samples × 10 cols becomes
              ~30 visible rows × 10 cols of actual DOM. */}
          <div
            className="hs-body"
            style={{
              height: `${totalHeight}px`,
              position: "relative",
              minWidth: `${totalWidth}px`,
            }}
          >
            {virtualRows.map((vrow) => {
              const idx = vrow.index;
              const item = sortedItems[idx];
              if (!item) return null;
              const meas = perSample?.get(item.sample_id) ?? [];
              const byOrd = byOrdBySample.get(item.sample_id) ?? EMPTY_BY_ORD;
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
              return (
                <div
                  key={item.sample_id}
                  className={`hs-row${liveSortActive ? " sync-live" : ""}`}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${vrow.start}px)`,
                    height: `${ROW_HEIGHT}px`,
                    display: "grid",
                    gridTemplateColumns: gridTemplate,
                  }}
                >
                  {columns.map((col) => {
                    const folded = persisted.folded.includes(col.id);
                    const wrapped =
                      persisted.wrapped.includes(col.id) && wrappable(col);
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
                        key={col.id}
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
                  })}
                </div>
              );
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
        <HardSamplesFooter
          scope={scope}
          onScopeChange={onScopeChange}
          syncLive={persisted.syncLive}
          onToggleSyncLive={() =>
            setPersisted((p) => ({ ...p, syncLive: !p.syncLive }))
          }
          hideUnmeasured={persisted.hideUnmeasured}
          onToggleHideUnmeasured={() =>
            setPersisted((p) => ({ ...p, hideUnmeasured: !p.hideUnmeasured }))
          }
          datasetName={datasetName}
          measuredCount={measuredCount}
          unmeasuredCount={unmeasuredCount}
          datasetSplitTest={datasetSplitTest}
          onResetLayout={resetLayout}
        />
      </div>

      {popover && (
        <HardSamplesPopover popover={popover} onClose={() => setPopover(null)} />
      )}

      {hoverTip && <HardSamplesHeatTip tip={hoverTip} />}
    </div>
  );
}
