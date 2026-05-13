"use client";
import { useEffect, useMemo, useState, type CSSProperties, type PointerEvent } from "react";
import { type DatasetItem } from "@/lib/api";
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

export interface MeasurementDot {
  hit: boolean;
  ord: number;
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
}

type ColId =
  | "rank"
  | "sample_id"
  | "measurements"
  | "surprise"
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
  { id: "surprise",      label: "Surprise",   align: "right",  numeric: true  },
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
}

const EMPTY_PERSISTED: PersistedState = { widths: {}, folded: [], wrapped: [] };

function loadPersisted(): PersistedState {
  if (typeof window === "undefined") return EMPTY_PERSISTED;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return EMPTY_PERSISTED;
    const p = JSON.parse(raw) as PersistedState;
    return {
      widths:  p.widths  ?? {},
      folded:  p.folded  ?? [],
      wrapped: p.wrapped ?? [],
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

// Surprise (miss-probability) → hue. 0 = cool green (always-hit), 0.5 =
// neutral grey (no signal yet), 1 = warm red (always-miss).
function surpriseStyle(s: number): CSSProperties {
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
    case "surprise":
      return {
        text: item.surprise.toFixed(2),
        raw: item.surprise,
        style: surpriseStyle(item.surprise),
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
): number {
  const headerCh = col.label.length + HEADER_PADDING_CH;
  let maxCh = headerCh;
  if (col.id === "rank") {
    // Rank shows row position; longest is items.length digits.
    maxCh = Math.max(headerCh, String(Math.max(1, items.length)).length + 1);
  } else if (col.id === "measurements") {
    // Dots column — sized by dot count (12 px stride incl. gap), capped
    // so the column stays narrow. We measure in *pixels* directly here
    // instead of in characters; return early to skip the char→px math.
    let maxDots = 0;
    for (const item of items) {
      const n = perSample?.get(item.sample_id)?.length ?? 0;
      if (n > maxDots) maxDots = n;
    }
    const cappedDots = Math.min(maxDots, 20);
    return Math.max(60, 16 + cappedDots * 8);
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

  const autoWidths = useMemo<Partial<Record<ColId, number>>>(() => {
    if (items.length === 0) return {};
    const w: Partial<Record<ColId, number>> = {};
    for (const col of columns) {
      w[col.id] = autoWidthFor(col, items, live, perSample);
    }
    return w;
    // `live` is read inside via cellFor; `liveSignature` captures the set of
    // measured samples so we recompute on cardinality change but not on every
    // value tick. Suppress the exhaustive-deps lint for this trade-off.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, liveSignature, columns, perSampleSig]);

  const sortedItems = useMemo(() => {
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
  }, [items, sortBy, live, perSample]);

  const widthFor = (col: ColDef): number => {
    if (persisted.folded.includes(col.id)) return FOLDED_WIDTH;
    return persisted.widths[col.id] ?? autoWidths[col.id] ?? 80;
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
            className="hs-grid"
            style={{
              gridTemplateColumns: gridTemplate,
              minWidth: `${totalWidth}px`,
            }}
          >
            {columns.map((col) => {
              const folded = persisted.folded.includes(col.id);
              const sorted = sortBy?.col === col.id ? sortBy.dir : null;
              const isRank = col.id === "rank";
              const headerTitle = folded
                ? "Unfold column"
                : isRank
                  ? RANK_HINT
                  : `Sort by ${col.label}`;
              return (
                <div
                  key={col.id}
                  className={`hs-header${folded ? " folded" : ""}${sorted ? " sorted" : ""}${isRank ? " rank" : ""}`}
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
                    style={cell.style}
                    title={folded ? undefined : cell.title}
                    onClick={onClick}
                  >
                    {folded ? "" : isMeas ? (
                      meas.length === 0 ? (
                        <span className="hs-heat-empty">—</span>
                      ) : (
                        <span className="hs-cell-meas-strip">
                          {meas.map((m, i) => (
                            <span
                              key={i}
                              className={`hs-heat-dot ${m.hit ? "hit" : "miss"}`}
                              title={`R${Math.floor(m.ord / 1000)} cand ${m.ord % 1000} · ${m.hit ? "HIT" : "MISS"}`}
                            />
                          ))}
                        </span>
                      )
                    ) : (
                      cell.text
                    )}
                  </div>
                );
              });
            })}
          </div>
        </div>
        <div className="hs-footer">
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
