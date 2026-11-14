import { useCallback, useMemo, useState, type ReactNode } from "react";
import type { ActiveElement, ChartEvent } from "chart.js";
import styles from "./ChartReading.module.css";

// A canvas paints no detail over its own marks (components/CLAUDE.md): the column a pointer rests on,
// or a tap pinned, is read in the DOM under the chart. A second tap on the pinned column clears it.
export function useChartReading() {
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
  return { at: hover ?? pinned, events };
}

// Quieter than the data: a footnote under the chart, holding its height while nothing is read.
export function ChartReading({ children, hint }: { children: ReactNode | null; hint: string }) {
  return (
    <p className={styles.reading} role="status">
      {children ?? <span className={styles.hint}>{hint}</span>}
    </p>
  );
}
