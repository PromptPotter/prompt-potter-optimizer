"use client";
import { useMemo, useRef, type CSSProperties } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useHardSamples } from "@/lib/hard-samples";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { MeasHeatCell } from "./MeasHeatCell";
import { heatLayout, ordIndexToXCss } from "@/lib/heat-canvas";
import {
  cellFor,
  headerTitle,
  ORDER_COLUMN,
  type CellValue,
  type HeatDot,
} from "./columns";
import { HardSamplesFooter } from "./HardSamplesFooter";
import { HardSamplesHeatTip, HardSamplesPopover } from "./HardSamplesPopover";
import { useHardSamplesTableModel, wrappable } from "./useHardSamplesTableModel";

interface Props {
  // Per-sample chronological measurement dots. When supplied, the table
  // shows a "measurements" column with one dot per measurement; when
  // omitted, the column is hidden.
  perSample?: Map<number, HeatDot[]>;
}

export function HardSamplesTable({ perSample }: Props) {
  const {
    datasetName,
    // The served per-sample series — what the Fitness column reports on. Kept apart
    // from `perSample`, which folds in the in-flight tail for the strip and is
    // therefore the one thing each call site builds for itself.
    archivePerSample: servedSeries,
    items: datasetItems,
    measuredCount,
    unmeasuredCount,
    splitTest: datasetSplitTest,
    rankedBy,
    rankedByPick,
    setRankedBy,
    scope,
    setScope,
    stale: datasetStale,
  } = useHardSamples();
  // `isLive` (status === "live") gates the row-scoring blink. When the
  // optimizer process dies, the open set is stranded in dashboard.json;
  // `isLive` goes false once freshness lapses, so the blink stops on its own.
  const { dash, isLive } = useDashboard();
  // Every sample in flight, so look-ahead blinks all N rows rather than the cursor's one.
  const openSampleIds = useMemo(
    () => new Set(dash?.open_sample_ids ?? []),
    [dash?.open_sample_ids],
  );
  const m = useHardSamplesTableModel({ datasetItems, perSample, servedSeries });
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

  const liveOrder = liveSortActive && rankedBy ? ORDER_COLUMN[rankedBy] : null;

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
    <>
      <div
        className="hs-block"
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
              // The marker follows the SERVED ranking, and names none until it lands:
              // which key the rows are in is not knowable before then.
              const sorted = liveSortActive
                ? col.id === liveOrder?.col
                  ? "desc"
                  : null
                : sortBy?.col === col.id
                  ? sortBy.dir
                  : null;
              const isRank = col.id === "rank";
              const title = headerTitle(col, { folded, liveSortActive, liveOrder });
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
                    title={title}
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
              // Membership rides `open_sample_ids` and nothing else: the producer opens and
              // closes that set on both edges, so it already answers "is this row in flight".
              // It is not `current_sample_id` — that one is the walk's CURSOR and names the
              // OLDEST open sample, so under look-ahead it lit one row of N. And it is not
              // conjoined with `dash.state`, which is the ACTIVITY word over a wider
              // vocabulary than "scoring": the whole origin panel runs under `origin` and the
              // gap between two samples under `between_samples`, so that conjunct went dark
              // for the longest phase of a run and flickered off between samples.
              // `isLive` stays — it is the freshness gate, so an open set stranded by a
              // killed producer stops blinking on its own.
              const isRunning = isLive && openSampleIds.has(item.sample_id);
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
                      : cellFor(col.id, item, meas, servedSeries?.get(item.sample_id) ?? null);
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
                              onHover={(ord, fitness, x, y) => setHoverTip({ ord, fitness, x, y })}
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
          onScopeChange={setScope}
          syncLive={persisted.syncLive}
          rankedBy={rankedBy}
          rankedByPick={rankedByPick}
          onRankedByChange={setRankedBy}
          onToggleSyncLive={() => setPersisted((p) => ({ ...p, syncLive: !p.syncLive }))}
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

      {popover && <HardSamplesPopover popover={popover} onClose={() => setPopover(null)} />}

      {hoverTip && <HardSamplesHeatTip tip={hoverTip} />}
    </>
  );
}
