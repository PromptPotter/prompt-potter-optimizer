import { useCallback, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import type { ActiveElement, ChartEvent } from "chart.js";
import styles from "./ChartReading.module.css";

// A canvas paints no detail over its own marks (components/CLAUDE.md): the column a pointer rests on,
// or a tap pinned, is read in the DOM under the chart. A second tap on the pinned column clears it.
// A painted surface is a control: its frame takes focus, ← and → step the pinned column over its
// `columns`, and Escape clears it.
export function useChartReading(columns: number) {
  const [hover, setHover] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
  const onHover = useCallback((evt: ChartEvent, elements: ActiveElement[]) => {
    // Chart.js files a finger's drag as "mousemove": only a mouse's own event hovers.
    if (evt.type === "mouseout") setHover(null);
    else if (evt.type === "mousemove" && evt.native instanceof MouseEvent) {
      setHover(elements[0]?.index ?? null);
    }
  }, []);
  // A tap is a click after the browser's compat mousemove, so the click owns the line and drops the
  // hover it raised; else a phone's second tap could never clear it.
  const onClick = useCallback((_evt: ChartEvent, elements: ActiveElement[]) => {
    const at = elements[0]?.index ?? null;
    setHover(null);
    setPinned((p) => (at === null || p === at ? null : at));
  }, []);
  const events = useMemo(() => ({ onHover, onClick }), [onHover, onClick]);
  const at = hover ?? pinned;
  const onKeyDown = useCallback(
    (evt: KeyboardEvent) => {
      const step = evt.key === "ArrowRight" ? 1 : evt.key === "ArrowLeft" ? -1 : 0;
      if (step === 0 && evt.key !== "Escape") return;
      evt.preventDefault();
      setHover(null);
      if (step === 0) setPinned(null);
      else if (at === null) setPinned(step > 0 ? 0 : columns - 1);
      else setPinned(Math.min(columns - 1, Math.max(0, at + step)));
    },
    [at, columns],
  );
  // Spread on the chart's frame; a chart with no column is no stop.
  const frame = useMemo(
    () =>
      columns > 0
        ? { tabIndex: 0, role: "group", "aria-keyshortcuts": "ArrowLeft ArrowRight Escape", onKeyDown }
        : {},
    [columns, onKeyDown],
  );
  return { at, events, frame };
}

// Quieter than the data: a footnote under the chart, holding its height while nothing is read.
export function ChartReading({ children, hint }: { children: ReactNode | null; hint: string }) {
  return (
    <p className={styles.reading} role="status">
      {children ?? <span className={styles.hint}>{hint}</span>}
    </p>
  );
}
