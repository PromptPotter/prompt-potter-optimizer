"use client";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent,
  type UIEvent,
} from "react";
import { type DatasetItem, type HardSamplesScope } from "@/lib/api";
import { liveL1Candidates, type DashboardSnapshot } from "@/lib/poll";

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

// Heat-map cell sizing. Cells flex between MIN_CELL_PX (vertical lines
// when the column is squeezed) and MAX_CELL_PX (the natural square). When
// the column is narrower than ``N × MIN_CELL_PX`` the strip overflows and
// a single sticky scrollbar at the column's bottom scrolls every row in
// sync — never let a measurement disappear.
const MIN_CELL_PX = 1;
const MAX_CELL_PX = 8;

export interface MeasurementDot {
  hit: boolean;
  // Composite lex-sortable key. Equal ``ord`` values across rows share
  // a roster column so the Meas heat-map aligns vertically.
  ord: string;
}

interface Props {
  dash: DashboardSnapshot | null;
  // Per-sample chronological measurement dots. When supplied, the table
  // shows a "measurements" column with one dot per measurement; when
  // omitted, the column is hidden.
  perSample?: Map<number, MeasurementDot[]>;
  // Compact = tiny default height (~3 rows visible). User can still drag
  // the resize handle to grow it. Off = standard preset height.
  compact?: boolean;
  datasetName: string | null;
  datasetItems: DatasetItem[];
  datasetTrainCount: number;
  datasetTestCount: number;
  // Data scope toggle. Workspace = cross-cycle archive Rasch (default,
  // matches /datasets/{name}/preview's old behaviour). Campaign = this
  // cycle only, read from campaigns/{cycle_id}/hard_samples_campaign.json.
  // Owner (DashboardPane) refetches the preview when scope changes.
  scope?: HardSamplesScope;
  onScopeChange?: (s: HardSamplesScope) => void;
}

type ColId =
  | "rank"
  | "sample_id"
  | "measurements"
  | "miss_prob"
  | "pick_score"
  | "n_obs"
  | "task"
  | "query"
  | "ground_truth"
  | "prediction"
  | "hit"
  | "cached"
  | "time_s"
  | "terminated_at"
  | "input_tokens"
  | "output_tokens";

interface ColDef {
  id: ColId;
  label: string;
  align: "left" | "right" | "center";
  numeric: boolean;
}

// Order is the visual left-to-right column order. Sortable columns receive
// `numeric: true` for right-align + tabular-nums; wrap-toggle only appears on
// `align: "left"` columns since other alignments are always short cells.
const COLUMNS: ColDef[] = [
  { id: "rank",          label: "#",          align: "right",  numeric: true  },
  { id: "sample_id",     label: "ID",         align: "right",  numeric: true  },
  { id: "measurements",  label: "Meas",       align: "left",   numeric: true  },
  { id: "miss_prob",     label: "P(miss)",    align: "right",  numeric: true  },
  { id: "pick_score",    label: "Pick",       align: "right",  numeric: true  },
  { id: "n_obs",         label: "Tries",      align: "right",  numeric: true  },
  { id: "task",          label: "Task",       align: "left",   numeric: false },
  { id: "query",         label: "Input",      align: "left",   numeric: false },
  { id: "ground_truth",  label: "Output",     align: "left",   numeric: false },
  { id: "prediction",    label: "Predicted",  align: "left",   numeric: false },
  { id: "hit",           label: "Hit",        align: "center", numeric: false },
  { id: "cached",        label: "Cache",      align: "center", numeric: false },
  { id: "time_s",        label: "Time",       align: "right",  numeric: true  },
  { id: "terminated_at", label: "Stopped at", align: "left",   numeric: false },
  { id: "input_tokens",  label: "In tok",     align: "right",  numeric: true  },
  { id: "output_tokens", label: "Out tok",    align: "right",  numeric: true  },
];

interface LiveEntry {
  prediction?: string;
  hit?: boolean;
  cached?: boolean;
  time_s?: number;
  terminated_at?: string;
  input_tokens?: number;
  output_tokens?: number;
}

// Drill into dashboard.json for the latest per-sample row across all
// candidates of the current round. Multiple candidates may have measured
// the same sample — last write wins, which gives the freshest result on the
// fly without taking a position on "which candidate is best".
//
// Note: during a live round the projection writes samples as compact strings
// (`fmt_sample_line` in live_dashboard.py). Strings have no `sample_id`, so the
// strict typeof check below skips them and the map stays empty until the round
// completes and full dicts land. Live columns then show em-dashes mid-round.
function extractLive(dash: DashboardSnapshot | null): Map<number, LiveEntry> {
  const out = new Map<number, LiveEntry>();
  for (const c of liveL1Candidates(dash)) {
    for (const s of c.samples ?? []) {
      if (typeof s !== "object" || s == null) continue;
      const sid = typeof s.sample_id === "number" ? s.sample_id : null;
      if (sid == null) continue;
      out.set(sid, {
        prediction:    s.prediction,
        hit:           s.hit,
        cached:        s.cached,
        time_s:        s.time_s,
        terminated_at: s.terminated_at,
        input_tokens:  s.input_tokens,
        output_tokens: s.output_tokens,
      });
    }
  }
  return out;
}

interface PersistedState {
  widths: Partial<Record<ColId, number>>;
  folded: ColId[];
  wrapped: ColId[];
  // When true, the table sort follows ``dash.hard_sample_order`` (the
  // Rasch δ_s ranking refreshed per-candidate by the optimizer) and
  // header-click sorting is suppressed. Default ON — operator opted into
  // the "real time mirror" framing.
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

function fmtBool(v: boolean | undefined): string {
  if (v === undefined) return "—";
  return v ? "✓" : "✗";
}

function fmtNum(v: number | undefined, digits = 0): string {
  if (v === undefined || !Number.isFinite(v)) return "—";
  return v.toFixed(digits);
}

interface CellValue {
  text: string;
  raw: string | number | boolean | null;
  title?: string;
  style?: CSSProperties;
  className?: string;
}

// Cell value for non-rank columns. Rank is computed inline in the render loop
// from the row's position in `sortedItems`.
function cellFor(
  col: ColId,
  item: DatasetItem,
  live: LiveEntry | undefined,
  measCount: number,
): CellValue {
  switch (col) {
    case "rank":
      return { text: "", raw: null };
    case "sample_id":
      return { text: String(item.sample_id), raw: item.sample_id };
    case "measurements":
      // Sort key is the measurement count; visual rendering happens
      // separately in the cell render loop (dots JSX, not text).
      return { text: measCount > 0 ? String(measCount) : "—", raw: measCount };
    case "miss_prob":
      return {
        text: item.miss_prob.toFixed(2),
        raw: item.miss_prob,
        style: missProbStyle(item.miss_prob),
      };
    case "pick_score":
      // Chernoff info in nats — fit's typically in (0, ~0.7). Three
      // decimals so equal-looking-but-actually-different values don't
      // collapse visually. ``—`` when scope=workspace (no seed concept)
      // or the sample is unmeasured.
      return {
        text: item.pick_score !== null ? item.pick_score.toFixed(3) : "—",
        raw: item.pick_score,
      };
    case "n_obs":
      // ``item.n_obs`` is the lifetime archive count for this backend_id
      // (cross-cycle, cross-session — see hard_sample_archive.py). The
      // operator reads "Tries" as in-cycle tries; use the cycle-scoped
      // measurement count already aggregated for the Meas column instead.
      return { text: String(measCount), raw: measCount };
    case "task": {
      const t = item.task ?? "";
      return { text: t || "—", raw: t, title: t || undefined };
    }
    case "query":
      return { text: item.query, raw: item.query, title: item.query };
    case "ground_truth":
      return { text: item.ground_truth, raw: item.ground_truth, title: item.ground_truth };
    case "prediction": {
      const v = live?.prediction ?? "";
      return { text: v || "—", raw: v, title: v || undefined };
    }
    case "hit":
      return {
        text: fmtBool(live?.hit),
        raw: live?.hit ?? null,
        className:
          live?.hit === true ? "hs-cell-hit" : live?.hit === false ? "hs-cell-miss" : "",
      };
    case "cached":
      return {
        text: fmtBool(live?.cached),
        raw: live?.cached ?? null,
        className: live?.cached ? "hs-cell-cached" : "",
      };
    case "time_s":
      return { text: live?.time_s !== undefined ? `${fmtNum(live.time_s, 2)}s` : "—", raw: live?.time_s ?? null };
    case "terminated_at": {
      const v = live?.terminated_at ?? "";
      return { text: v || "—", raw: v, title: v || undefined };
    }
    case "input_tokens":
      return { text: live?.input_tokens !== undefined ? String(live.input_tokens) : "—", raw: live?.input_tokens ?? null };
    case "output_tokens":
      return { text: live?.output_tokens !== undefined ? String(live.output_tokens) : "—", raw: live?.output_tokens ?? null };
  }
}

// Auto-size a column by sampling its rendered text. Floor is the header label
// length (so headers never get clipped); ceiling is MAX_AUTO_CH (long text
// wraps or expands via popover instead of forcing a 1000 px column).
function autoWidthFor(
  col: ColDef,
  items: DatasetItem[],
  live: Map<number, LiveEntry>,
  perSample: Map<number, MeasurementDot[]> | undefined,
  ordColsCount: number,
): number {
  const headerCh = col.label.length + HEADER_PADDING_CH;
  let maxCh = headerCh;
  if (col.id === "rank") {
    // Rank shows row position; longest is items.length digits.
    maxCh = Math.max(headerCh, String(Math.max(1, items.length)).length + 1);
  } else if (col.id === "measurements") {
    // Roster: cells flex into whatever width the column has — 8 px max
    // (set in the inline ``maxWidth`` below) so wide columns don't bloat
    // the squares, down to 1 px vertical lines when crowded. Auto-width
    // here only sets the initial column size: target 8 px per cell up to
    // a 280 px ceiling so the Meas column doesn't dominate the layout
    // when ``ordCols`` runs into the hundreds. Operator-resizable from
    // there via the header drag handle.
    return Math.max(60, Math.min(12 + ordColsCount * 8, 280));
  } else {
    for (const item of items) {
      const count = perSample?.get(item.sample_id)?.length ?? 0;
      const text = cellFor(col.id, item, live.get(item.sample_id), count).text;
      const ch = Math.min(text.length, MAX_AUTO_CH);
      if (ch > maxCh) maxCh = ch;
    }
  }
  return Math.max(MIN_WIDTH, Math.round(maxCh * CHAR_PX + CELL_PADDING_PX));
}

const RANK_HINT = "Position in current sort order — click to clear sort";

export function HardSamplesTable({
  dash,
  perSample,
  compact,
  datasetName,
  datasetItems,
  datasetTrainCount,
  datasetTestCount,
  scope,
  onScopeChange,
}: Props) {
  // When perSample is undefined (legacy callers), drop the dots column
  // entirely so the table renders exactly as before.
  const columns = useMemo<ColDef[]>(
    () => COLUMNS.filter((c) => perSample || c.id !== "measurements"),
    [perSample],
  );
  const items = datasetItems;
  const trainCount = datasetTrainCount;
  const testCount = datasetTestCount;

  const [persisted, setPersisted] = useState<PersistedState>(() => loadPersisted());
  const [sortBy, setSortBy] = useState<{ col: ColId; dir: "asc" | "desc" } | null>(null);
  const [popover, setPopover] = useState<{ col: ColId; sampleId: number; text: string } | null>(null);
  // Currently-highlighted Meas roster column. Click a cell → its ``ord``
  // lights up across every row; arrows pan left/right; Escape clears.
  const [selectedOrd, setSelectedOrd] = useState<string | null>(null);

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

  const live = useMemo(() => extractLive(dash), [dash]);

  // Stable signature of which sample_ids have live data. Auto-widths recompute
  // when this changes (first arrival of live data; new samples join the round)
  // but NOT when only values inside known samples update — that would jitter
  // column widths every poll.
  const liveSignature = useMemo(
    () => [...live.keys()].sort((a, b) => a - b).join(","),
    [live],
  );

  // Stable signature of measurement counts per sample. Drives auto-width
  // recompute for the dots column on real changes only.
  const perSampleSig = useMemo(() => {
    if (!perSample) return "";
    return [...perSample.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([sid, ms]) => `${sid}:${ms.length}`)
      .join(",");
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
      w[col.id] = autoWidthFor(col, items, live, perSample, ordCols.length);
    }
    return w;
    // `live` is read inside via cellFor; `liveSignature` captures the set of
    // measured samples so we recompute on cardinality change but not on every
    // value tick. Suppress the exhaustive-deps lint for this trade-off.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, liveSignature, columns, perSampleSig, ordCols.length]);

  // Live-sort: when the operator's "sync with live sort" tick is on and
  // the loop has emitted at least one Rasch fit, mirror that ordering
  // verbatim. Samples missing from ``hard_sample_order`` (fresh additions
  // the sorter hasn't seen yet) sink to the end in sample_id order.
  const hardOrder = dash?.hard_sample_order;
  const liveSortActive =
    persisted.syncLive && Array.isArray(hardOrder) && hardOrder.length > 0;
  const sortedItems = useMemo(() => {
    if (liveSortActive) {
      const rank = new Map<number, number>();
      const order = hardOrder ?? [];
      for (let i = 0; i < order.length; i++) rank.set(order[i], i);
      const sink = order.length;
      return [...items].sort((a, b) => {
        const ra = rank.get(a.sample_id) ?? sink;
        const rb = rank.get(b.sample_id) ?? sink;
        if (ra !== rb) return ra - rb;
        return a.sample_id - b.sample_id;
      });
    }
    if (!sortBy) return items;
    const { col, dir } = sortBy;
    const sign = dir === "asc" ? 1 : -1;
    const keyOf = (it: DatasetItem): number | string => {
      const count = perSample?.get(it.sample_id)?.length ?? 0;
      const v = cellFor(col, it, live.get(it.sample_id), count).raw;
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
  }, [items, sortBy, live, perSample, liveSortActive, hardOrder]);

  const widthFor = (col: ColDef): number => {
    if (persisted.folded.includes(col.id)) return FOLDED_WIDTH;
    return persisted.widths[col.id] ?? autoWidths[col.id] ?? 80;
  };

  // Heat-map gap: 1 px between cells when the Meas column has room for
  // every cell at the natural MAX_CELL_PX width, 0 px when squeezed.
  // Without this the squares run together once cells start shrinking —
  // but with it always on, the gaps eat the budget at narrow widths and
  // force cells to 0 px before the column is actually full. The
  // breakpoint is ``N × (MAX + 1) − 1`` (N cells + N−1 1-px gaps).
  const measCol = columns.find((c) => c.id === "measurements");
  const measColIdx = columns.findIndex((c) => c.id === "measurements");
  const measColWidth = measCol ? widthFor(measCol) : 0;
  const measGapPx =
    ordCols.length > 1 && measColWidth >= ordCols.length * (MAX_CELL_PX + 1) - 1 ? 1 : 0;
  const measMaxWidthPx =
    ordCols.length * MAX_CELL_PX + Math.max(0, ordCols.length - 1) * measGapPx;
  // Width every cell needs at its minimum size; if the column is
  // narrower, strips overflow and the master scrollbar takes over.
  const measMinStripPx =
    ordCols.length * MIN_CELL_PX + Math.max(0, ordCols.length - 1) * measGapPx;
  const needsHScroll = ordCols.length > 0 && measMinStripPx > measColWidth;

  // Sticky-bottom master scrollbar synced with every row strip. Each
  // strip is overflow-x:auto with the native scrollbar hidden; wheel /
  // touch / keyboard scroll on any strip propagates here, and the
  // master's scrollbar drag propagates back. ``isSyncingRef`` breaks
  // the would-be feedback loop without throttling actual user input.
  const stripRefs = useRef<Map<number, HTMLSpanElement>>(new Map());
  const masterScrollRef = useRef<HTMLDivElement>(null);
  const isSyncingRef = useRef(false);
  const setStripRef = (sid: number) => (el: HTMLSpanElement | null) => {
    if (el) stripRefs.current.set(sid, el);
    else stripRefs.current.delete(sid);
  };
  const onMeasScroll = (e: UIEvent<HTMLElement>) => {
    if (isSyncingRef.current) return;
    isSyncingRef.current = true;
    const sx = e.currentTarget.scrollLeft;
    for (const el of stripRefs.current.values()) {
      if (el !== e.currentTarget && Math.abs(el.scrollLeft - sx) > 0.5) {
        el.scrollLeft = sx;
      }
    }
    const m = masterScrollRef.current;
    if (m && m !== e.currentTarget && Math.abs(m.scrollLeft - sx) > 0.5) {
      m.scrollLeft = sx;
    }
    requestAnimationFrame(() => {
      isSyncingRef.current = false;
    });
  };

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

  const total = trainCount + testCount;
  const tag = datasetName ? `${datasetName} · ` : "";
  const wrappable = (col: ColDef): boolean => col.align === "left";

  return (
    <div className="hs-zone">
      <div className={`hs-block${compact ? " compact" : ""}`}>
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
              const sorted = !liveSortActive && sortBy?.col === col.id ? sortBy.dir : null;
              const isRank = col.id === "rank";
              const headerTitle = folded
                ? "Unfold column"
                : liveSortActive
                  ? "Synced to live Rasch sort — toggle off the sync chip to sort manually"
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
              const liveEntry = live.get(item.sample_id);
              const meas = perSample?.get(item.sample_id) ?? [];
              // Build the per-row ord → dot lookup once so the Meas
              // render below is O(ordCols) without an inner .find().
              const byOrd = new Map<string, MeasurementDot>();
              for (const m of meas) byOrd.set(m.ord, m);
              // Mark every cell in the row currently being scored so the
              // soft-blink keyframe (globals.css) animates the whole row,
              // not just one cell. Independent of the sort-sync toggle —
              // operators sorting manually still want to see "this is the
              // row the loop is on right now".
              const isRunning =
                typeof dash?.current_sample_id === "number" &&
                item.sample_id === dash.current_sample_id;
              return columns.map((col) => {
                const folded = persisted.folded.includes(col.id);
                const wrapped = persisted.wrapped.includes(col.id) && wrappable(col);
                const isRank = col.id === "rank";
                const isMeas = col.id === "measurements";
                const cell = isRank
                  ? ({ text: String(idx + 1), raw: idx + 1 } as CellValue)
                  : cellFor(col.id, item, liveEntry, meas.length);
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
                    className={`hs-cell${folded ? " folded" : ""}${wrapped ? " wrapped" : ""}${isRank ? " rank" : ""}${isMeas ? " meas" : ""}${cell.className ? ` ${cell.className}` : ""}`}
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
                        <span
                          ref={setStripRef(item.sample_id)}
                          onScroll={onMeasScroll}
                          className="hs-cell-meas-strip"
                          style={{
                            // ``minmax(MIN, 1fr)`` floors every cell at
                            // MIN_CELL_PX so cells never disappear when
                            // squeezed — the strip overflows the column
                            // instead and the sticky master scrollbar
                            // below scrolls every row in sync.
                            // ``maxWidth`` caps the strip at the natural
                            // MAX_CELL_PX/cell width so wide columns
                            // don't stretch the squares.
                            gridTemplateColumns: `repeat(${ordCols.length}, minmax(${MIN_CELL_PX}px, 1fr))`,
                            maxWidth: `${measMaxWidthPx}px`,
                            gap: `${measGapPx}px`,
                          }}
                        >
                          {ordCols.map((ord) => {
                            const m = byOrd.get(ord);
                            const sel = ord === selectedOrd;
                            return (
                              <span
                                key={ord}
                                className={`hs-heat-cell ${m ? (m.hit ? "hit" : "miss") : "empty"}${sel ? " selected" : ""}`}
                                title={m ? `${m.hit ? "HIT" : "MISS"} · ${ord}` : ord}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setSelectedOrd((cur) => (cur === ord ? null : ord));
                                }}
                              />
                            );
                          })}
                          {isRunning && (
                            <span
                              key="pending"
                              className="hs-heat-cell pending"
                              title="Currently scoring"
                            />
                          )}
                        </span>
                      )
                    ) : (
                      cell.text
                    )}
                  </div>
                );
              });
            })}

            {needsHScroll && measColIdx >= 0 && (
              <div
                className="hs-meas-master-scroll-wrap"
                style={{ gridColumn: `${measColIdx + 1} / span 1` }}
              >
                <div
                  ref={masterScrollRef}
                  className="hs-meas-master-scroll"
                  onScroll={onMeasScroll}
                  aria-label="Heat-map horizontal scroll"
                >
                  <div
                    className="hs-meas-master-scroll-inner"
                    style={{ width: `${measMinStripPx}px` }}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
        <div className="hs-footer">
          {scope && onScopeChange ? (
            <div
              className="hs-scope-toggle"
              role="radiogroup"
              aria-label="Hard-sample data scope"
              title={
                scope === "workspace"
                  ? "Showing cross-cycle archive evidence. Toggle to this cycle's evidence only."
                  : "Showing this cycle's evidence only. Toggle to cross-cycle archive."
              }
            >
              <button
                type="button"
                role="radio"
                aria-checked={scope === "campaign"}
                className={`hs-scope-opt${scope === "campaign" ? " on" : ""}`}
                onClick={() => onScopeChange("campaign")}
              >
                Campaign
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={scope === "workspace"}
                className={`hs-scope-opt${scope === "workspace" ? " on" : ""}`}
                onClick={() => onScopeChange("workspace")}
              >
                Workspace
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
          <span className="hs-counts">
            {tag}Train {trainCount} · Test {testCount} · Total {total}
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
    </div>
  );
}
