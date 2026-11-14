import { Activity, useEffect, useRef, useState, type RefObject } from "react";
import { Button, IconClose } from "@/components/ui";
import type { FamilyId } from "@/lib/address";
import { useWorkspace } from "@/lib/state/workspace";
import { FAMILIES, openColumns } from "./families";

// The row scrolls, never `scrollIntoView`: that moves where Tab starts, past the skip link.
const show = (family: FamilyId) => {
  const v = document.getElementById(`view-${family}`);
  if (v?.parentElement) v.parentElement.scrollTo({ left: v.offsetLeft - v.parentElement.offsetLeft });
};

// The columns the row shows, by one observer that reveals what it sees: until it first answers, only
// *shown*, the column the workspace scrolled to, so no other column reads even at mount. Null where
// no observer exists, when every column shows.
function useOnScreen(
  row: RefObject<HTMLDivElement | null>,
  columns: string,
  shown: string | undefined,
): ReadonlySet<string> | null {
  const [onScreen, setOnScreen] = useState<ReadonlySet<string> | null>(() =>
    typeof IntersectionObserver === "undefined" ? null : new Set(shown === undefined ? [] : [shown]),
  );
  useEffect(() => {
    const el = row.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver(
      (entries) =>
        setOnScreen((prev) => {
          const next = new Set(prev);
          for (const { target, isIntersecting } of entries) {
            const { family } = (target as HTMLElement).dataset;
            if (family === undefined) continue;
            if (isIntersecting) next.add(family);
            else next.delete(family);
          }
          return next;
        }),
      { root: el },
    );
    for (const column of el.children) io.observe(column);
    return () => io.disconnect();
  }, [row, columns]);
  return onScreen;
}

// Every open view is a column and its own scroller, newest first: a phone swipes between them, a wide
// screen shows as many as fit. Past one, each column heads with its name and its close. A column off
// screen keeps its state and reads nothing (`<Activity>`); swiped into, it re-asks what moved meanwhile.
export function Views() {
  const { views, reveal, close } = useWorkspace();
  const columns = openColumns(views).reverse();
  const row = useRef<HTMLDivElement>(null);
  const onScreen = useOnScreen(
    row,
    columns.map((c) => c.family).join(" "),
    reveal?.family ?? columns[0]?.family,
  );
  // Snapping holds the column it was on, so each `open` brings its view into view.
  useEffect(() => void (reveal && show(reveal.family)), [reveal]);
  return (
    <div className="views" ref={row}>
      {columns.map(({ family, key }) => {
        const { label, pane } = FAMILIES[family];
        return (
          <div key={family} id={`view-${family}`} data-family={family} className="view">
            {columns.length > 1 && (
              <div className="view-head">
                <span>{label}</span>
                <Button variant="ghost" aria-label={`Close ${label}`} onClick={() => close(family)}>
                  <IconClose />
                </Button>
              </div>
            )}
            <Activity mode={onScreen === null || onScreen.has(family) ? "visible" : "hidden"}>
              {family === "chat" ? (
                pane!(key)
              ) : (
                <div className="content">
                  <div className="box-grid">{pane!(key)}</div>
                </div>
              )}
            </Activity>
          </div>
        );
      })}
    </div>
  );
}
