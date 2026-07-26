"use client";
// Table model for HardSamplesTable: owns column selection, persisted layout
// (localStorage), sort state, the Meas-roster ordinal universe + selection, and
// every derived projection (filtered/sorted ids, per-ord lookups, widths,
// geometry) plus the header/resize/fold handlers. The component consumes this
// and only renders.

import { useEffect, useMemo, useState, type PointerEvent } from "react";
import { type DatasetItem } from "@/lib/api";
import { useStableContent } from "@/lib/stable";
import { compareHardSamples } from "./hard-sample-order";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import {
  autoWidthFor,
  cellFor,
  COLUMNS,
  EMPTY_PERSISTED,
  FOLDED_WIDTH,
  MIN_WIDTH,
  STORAGE_KEY,
  type ColDef,
  type ColId,
  type HeatDot,
  type PersistedState,
} from "./columns";

// Text-wrap toggle applies to left-aligned text columns only — never the
// History column (a canvas, nothing to wrap).
export function wrappable(col: ColDef): boolean {
  return col.align === "left" && col.id !== "measurements";
}

interface HardSamplesTableModelInput {
  datasetItems: DatasetItem[];
  perSample?: Map<number, HeatDot[]>;
}

export function useHardSamplesTableModel({
  datasetItems,
  perSample,
}: HardSamplesTableModelInput) {
  // The Map identity churns every 2 s poll even when content is unchanged.
  // Stabilise it once at the top — every memo below that depends on perSample
  // (or values derived from it) then gates on real content change.
  const stablePerSample = useStableContent(perSample);
  // Drop columns with nothing to show: the History heat-map needs
  // `perSample`; the Task column needs at least one sample carrying a task
  // label (datasets without task families never populate it).
  const columns = useMemo<ColDef[]>(() => {
    const hasTask = datasetItems.some((it) => (it.task ?? "") !== "");
    return COLUMNS.filter(
      (c) => (stablePerSample || c.id !== "measurements") && (hasTask || c.id !== "task"),
    );
  }, [stablePerSample, datasetItems]);

  const [persisted, setPersisted] = useLocalStorage<PersistedState>(
    STORAGE_KEY,
    EMPTY_PERSISTED,
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

  useEffect(() => {
    if (!popover) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPopover(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [popover]);

  // Global ordinal universe — the union of every ord present across rows,
  // sorted lex. Each ord becomes one column in the Meas roster so rows
  // missing a given measurement show a blank cell at the same X as rows
  // that have it. This is what turns the old left-packed strip into a
  // proper heat-map.
  const ordCols = useMemo(() => {
    if (!stablePerSample) return [] as string[];
    const all = new Set<string>();
    for (const ms of stablePerSample.values()) for (const m of ms) all.add(m.ord);
    return [...all].sort();
  }, [stablePerSample]);

  const ordIndex = useMemo(() => {
    const m = new Map<string, number>();
    ordCols.forEach((ord, i) => m.set(ord, i));
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
      setSelectedOrd(ordCols[next]!);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedOrd, ordCols]);

  // Drop the selection render-phase when its ord vanishes (scope toggle,
  // cycle change). Converges: once null, the guard is false.
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
    if (!stablePerSample) return datasetItems;
    return datasetItems.filter((it) => (stablePerSample.get(it.sample_id)?.length ?? 0) > 0);
  }, [datasetItems, stablePerSample, persisted.hideUnmeasured]);

  const autoWidths = useMemo<Partial<Record<ColId, number>>>(() => {
    if (items.length === 0) return {};
    const w: Partial<Record<ColId, number>> = {};
    for (const col of columns) {
      w[col.id] = autoWidthFor(col, items, stablePerSample, ordCols.length);
    }
    return w;
  }, [items, columns, stablePerSample, ordCols.length]);

  // Live-sort ("Auto-sort"): the shared hard-sample ranking (`compareHardSamples`) —
  // the same comparator the heatmap uses, so the two surfaces cannot disagree.
  const liveSortActive = persisted.syncLive;
  const sortedIds = useMemo<number[]>(() => {
    const measuredIn = (it: DatasetItem): boolean =>
      (stablePerSample?.get(it.sample_id)?.length ?? 0) > 0;
    if (liveSortActive) {
      return [...items]
        .sort((a, b) => compareHardSamples(a, b, measuredIn))
        .map((it) => it.sample_id);
    }
    if (!sortBy) return items.map((it) => it.sample_id);
    const { col, dir } = sortBy;
    const sign = dir === "asc" ? 1 : -1;
    const keyOf = (it: DatasetItem): number | string => {
      const v = cellFor(col, it, stablePerSample?.get(it.sample_id) ?? []).raw;
      if (v === null) return Number.NEGATIVE_INFINITY;
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
  }, [liveSortActive, sortBy, items, stablePerSample]);

  // Index items by sample_id so the row loop projects via stable order
  // without a second sortedItems memo.
  const byId = useMemo(() => {
    const m = new Map<number, DatasetItem>();
    for (const it of items) m.set(it.sample_id, it);
    return m;
  }, [items]);

  // Per-sample ord → dot lookup, built once per stablePerSample change.
  const byOrdBySample = useMemo(() => {
    const out = new Map<number, Map<string, HeatDot>>();
    if (!stablePerSample) return out;
    for (const [sid, ms] of stablePerSample) {
      const m = new Map<string, HeatDot>();
      for (const x of ms) m.set(x.ord, x);
      out.set(sid, m);
    }
    return out;
  }, [stablePerSample]);
  const EMPTY_BY_ORD = useMemo(() => new Map<string, HeatDot>(), []);

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
      // the operator unlocks it via the sync chip.
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

  return {
    stablePerSample,
    columns,
    persisted,
    setPersisted,
    sortBy,
    liveSortActive,
    popover,
    setPopover,
    selectedOrd,
    setSelectedOrd,
    hoverTip,
    setHoverTip,
    items,
    sortedIds,
    byId,
    byOrdBySample,
    EMPTY_BY_ORD,
    ordCols,
    ordIndex,
    widthFor,
    measColWidth,
    measColFolded,
    measColLeft,
    gridTemplate,
    totalWidth,
    startResize,
    toggleFold,
    toggleWrap,
    handleHeaderClick,
    resetLayout,
  };
}
