"use client";
import { useRef, type CSSProperties } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { type DatasetItem, type HardSamplesScope } from "@/lib/api";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { MeasHeatCell } from "./MeasHeatCell";
import { heatLayout, ordIndexToXCss } from "@/lib/heat-canvas";
import { cellFor, RANK_HINT, type CellValue, type HeatDot } from "./columns";
import { HardSamplesFooter } from "./HardSamplesFooter";
import { HardSamplesHeatTip, HardSamplesPopover } from "./HardSamplesPopover";
import { useHardSamplesTableModel, wrappable } from "./useHardSamplesTableModel";

interface Props {
  // Per-sample chronological measurement dots. When supplied, the table
  // shows a "measurements" column with one dot per measurement; when
  // omitted, the column is hidden.
  perSample?: Map<number, HeatDot[]>;
  // Compact = tiny default height (~3 rows visible). User can still drag
  // the resize handle to grow it. Off = standard preset height.
  compact?: boolean;
  datasetName: string | null;
  datasetItems: DatasetItem[];
  datasetMeasuredCount: number;
  datasetUnmeasuredCount: number;
  datasetSplitTest: number | null;
  scope?: HardSamplesScope;
  onScopeChange?: (s: HardSamplesScope) => void;
  // Displayed roster is from a prior (unit, scope) and a fresh fetch is in
  // flight — the table dims via the `stale` data-attr but never blanks.
  datasetStale?: boolean;
}

export function HardSamplesTable({
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
  // `isLive` (status === "live") gates the row-scoring blink. When the
  // optimizer process dies, `current_sample_id` is stranded in dashboard.json;
  // `isLive` goes false once freshness lapses, so the blink stops on its own.
  const { dash, isLive } = useDashboard();
  const m = useHardSamplesTableModel({ datasetItems, perSample });
  const {
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
    sortedIds,
    byId,
    byOrdBySample,
    EMPTY_BY_ORD,
    ordCols,
    ordIndex,
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
  } = m;

  const scrollRef = useRef<HTMLDivElement>(null);
  const ROW_HEIGHT = 26;
  const virtualizer = useVirtualizer({
    count: sortedIds.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
  });
  const virtualRows = virtualizer.getVirtualItems();
  const totalHeight = virtualizer.getTotalSize();

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
              const sampleId = sortedIds[idx];
              const item = sampleId === undefined ? undefined : byId.get(sampleId);
              if (!item) return null;
              const meas = stablePerSample?.get(item.sample_id) ?? [];
              const byOrd = byOrdBySample.get(item.sample_id) ?? EMPTY_BY_ORD;
              // Mark every cell in the row currently being scored so the
              // soft-blink keyframe (app/styles/domains/hard-samples.css) animates the whole row,
              // not just one cell. Independent of the sort-sync toggle.
              // Gated on `isLive` + the scoring phase so a stranded
              // `current_sample_id` (process killed mid-sample, or a
              // non-scoring phase) never blinks.
              const isRunning =
                isLive &&
                dash?.state === "scoring" &&
                typeof dash.current_sample_id === "number" &&
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
                    // A clickable cell is keyboard-operable: folded cells
                    // unfold, expandable text cells open the read-out
                    // popover. Virtualization bounds this to visible rows,
                    // so the tab order never spans the whole roster.
                    const interactive = onClick != null;
                    return (
                      <div
                        key={col.id}
                        className={`hs-cell${folded ? " folded" : ""}${wrapped ? " wrapped" : ""}${isRank ? " rank" : ""}${isMeas ? " meas" : ""}`}
                        data-align={col.align}
                        data-running={isRunning ? "true" : undefined}
                        style={cell.style}
                        title={folded ? undefined : cell.title}
                        role={interactive ? "button" : undefined}
                        tabIndex={interactive ? 0 : undefined}
                        aria-label={interactive && folded ? "Unfold column" : undefined}
                        onClick={onClick}
                        onKeyDown={
                          interactive
                            ? (e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault();
                                  onClick();
                                }
                              }
                            : undefined
                        }
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
                              onSelectOrd={(ord) =>
                                setSelectedOrd((cur) => (cur === ord ? null : ord))
                              }
                              onHover={(ord, hit, x, y) => setHoverTip({ ord, hit, x, y })}
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
          onToggleSyncLive={() => setPersisted((p) => ({ ...p, syncLive: !p.syncLive }))}
          hideUnmeasured={persisted.hideUnmeasured}
          onToggleHideUnmeasured={() =>
            setPersisted((p) => ({ ...p, hideUnmeasured: !p.hideUnmeasured }))
          }
          datasetName={datasetName}
          measuredCount={datasetMeasuredCount}
          unmeasuredCount={datasetUnmeasuredCount}
          datasetSplitTest={datasetSplitTest}
          onResetLayout={resetLayout}
        />
      </div>

      {popover && <HardSamplesPopover popover={popover} onClose={() => setPopover(null)} />}

      {hoverTip && <HardSamplesHeatTip tip={hoverTip} />}
    </div>
  );
}
