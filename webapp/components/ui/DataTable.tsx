"use client";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cx } from "@/lib/cx";
import s from "./DataTable.module.css";

// THE table for every list of served rows. It never ORDERS anything: an ordering is a score
// (`webapp/CLAUDE.md` § Scoring authority).

export interface Column<T> {
  id: string;
  label: ReactNode;
  width: string;
  cell: (row: T) => ReactNode;
  align?: "start" | "end";
}

export interface RowGroup<T> {
  key: string;
  header: ReactNode;
  rows: T[];
}

type Item<T> = { kind: "group"; group: RowGroup<T> } | { kind: "row"; row: T };

const ROW_PX = 30;
const GROUP_PX = 34;
const VIRTUAL_FROM = 150;

export function DataTable<T>({
  columns,
  rows,
  groups,
  getRowId,
  activeRowId = null,
  onRowClick,
  ariaLabel,
  empty,
  expandedByDefault = false,
  maxHeight = "70vh",
  className,
}: {
  columns: readonly Column<T>[];
  // Exactly one of `rows` / `groups`.
  rows?: readonly T[];
  groups?: readonly RowGroup<T>[];
  getRowId: (row: T) => string;
  activeRowId?: string | null;
  onRowClick?: (row: T) => void;
  ariaLabel: string;
  empty?: ReactNode;
  expandedByDefault?: boolean;
  maxHeight?: string;
  className?: string;
}) {
  const [flipped, setFlipped] = useState<ReadonlySet<string>>(() => new Set());
  const isOpen = (key: string) => flipped.has(key) !== expandedByDefault;
  const toggle = (key: string) =>
    setFlipped((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const items = useMemo<Item<T>[]>(() => {
    if (!groups) return (rows ?? []).map((row) => ({ kind: "row", row }));
    const out: Item<T>[] = [];
    for (const g of groups) {
      out.push({ kind: "group", group: g });
      if (flipped.has(g.key) !== expandedByDefault) for (const row of g.rows) out.push({ kind: "row", row });
    }
    return out;
  }, [rows, groups, flipped, expandedByDefault]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtual = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (i) => (items[i]?.kind === "group" ? GROUP_PX : ROW_PX),
    overscan: 12,
  });
  const virtualized = items.length >= VIRTUAL_FROM;
  // A stepped-to row outside the mounted window would be active and unseen.
  const activeIndex =
    activeRowId == null
      ? -1
      : items.findIndex((it) => it.kind === "row" && getRowId(it.row) === activeRowId);
  useEffect(() => {
    if (virtualized && activeIndex >= 0) virtual.scrollToIndex(activeIndex, { align: "auto" });
  }, [virtualized, activeIndex, virtual]);
  const placed = virtualized
    ? virtual.getVirtualItems().map((v) => ({
        index: v.index,
        pos: { transform: `translateY(${v.start}px)`, height: v.size } as CSSProperties,
      }))
    : items.map((it, index) => ({
        index,
        pos: { height: it.kind === "group" ? GROUP_PX : ROW_PX } as CSSProperties,
      }));

  const template: CSSProperties = {
    gridTemplateColumns: columns.map((c) => c.width).join(" "),
  };

  return (
    <div className={cx(s.table, className)} role="table" aria-label={ariaLabel}>
      <div ref={scrollRef} className={s.scroll} style={{ maxHeight }}>
        <div className={s.head} role="row" style={template}>
          {columns.map((c) => (
            <span key={c.id} role="columnheader" className={cx(s.th, c.align === "end" && s.end)}>
              {c.label}
            </span>
          ))}
        </div>
        {items.length === 0 ? (
          <div className={s.empty}>{empty ?? "Nothing to show."}</div>
        ) : (
          <div
            className={cx(s.body, !virtualized && s.flow)}
            style={virtualized ? { height: virtual.getTotalSize() } : undefined}
          >
            {placed.map(({ index, pos }) => {
              const item = items[index];
              if (!item) return null;
              if (item.kind === "group") {
                const open = isOpen(item.group.key);
                return (
                  <button
                    key={`g:${item.group.key}`}
                    type="button"
                    className={cx(s.group, open && s.open)}
                    style={pos}
                    aria-expanded={open}
                    onClick={() => toggle(item.group.key)}
                  >
                    <span className={s.caret} aria-hidden="true">
                      {open ? "▾" : "▸"}
                    </span>
                    {item.group.header}
                  </button>
                );
              }
              const id = getRowId(item.row);
              return (
                <div
                  key={`r:${id}`}
                  role="row"
                  className={cx(s.row, onRowClick && s.clickable, id === activeRowId && s.active)}
                  style={{ ...pos, ...template }}
                  tabIndex={onRowClick ? 0 : undefined}
                  aria-current={id === activeRowId || undefined}
                  onClick={onRowClick ? () => onRowClick(item.row) : undefined}
                  onKeyDown={
                    onRowClick
                      ? (e) => {
                          if (e.key !== "Enter" && e.key !== " ") return;
                          e.preventDefault();
                          onRowClick(item.row);
                        }
                      : undefined
                  }
                >
                  {columns.map((c) => (
                    <span key={c.id} role="cell" className={cx(s.td, c.align === "end" && s.end)}>
                      {c.cell(item.row)}
                    </span>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
