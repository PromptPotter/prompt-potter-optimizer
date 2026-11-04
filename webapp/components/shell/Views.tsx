import { useEffect } from "react";
import { Button, IconClose } from "@/components/ui";
import type { FamilyId } from "@/lib/address";
import { useWorkspace } from "@/lib/workspace";
import { FAMILIES, openColumns } from "./families";

// The row scrolls, never `scrollIntoView`: that moves where Tab starts, past the skip link.
const show = (family: FamilyId) => {
  const v = document.getElementById(`view-${family}`);
  if (v?.parentElement) v.parentElement.scrollTo({ left: v.offsetLeft - v.parentElement.offsetLeft });
};

// Every open view is a column and its own scroller, newest first: a phone swipes between them, a wide
// screen shows as many as fit. Past one, each column heads with its name and its close.
export function Views() {
  const { views, reveal, close } = useWorkspace();
  const columns = openColumns(views).reverse();
  // Snapping holds the column it was on, so each `open` brings its view into view.
  useEffect(() => void (reveal && show(reveal.family)), [reveal]);
  return (
    <div className="views">
      {columns.map(({ family, key }) => {
        const { label, pane } = FAMILIES[family];
        return (
          <div key={family} id={`view-${family}`} className="view">
            {columns.length > 1 && (
              <div className="view-head">
                <span>{label}</span>
                <Button variant="ghost" aria-label={`Close ${label}`} onClick={() => close(family)}>
                  <IconClose />
                </Button>
              </div>
            )}
            {family === "chat" ? (
              pane!(key)
            ) : (
              <div className="content">
                <div className="box-grid">{pane!(key)}</div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
