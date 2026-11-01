"use client";
import { useEffect } from "react";
import { Button, IconClose, Said } from "@/components/ui";
import type { FamilyId } from "@/lib/address";
import { useViewedCourse } from "@/lib/lineage";
import { useWorkspace } from "@/lib/workspace";
import { FAMILIES } from "./families";

// The strip scrolls, never `scrollIntoView`: that moves where Tab starts, past the skip link.
const show = (family: FamilyId) => {
  const v = document.getElementById(`view-${family}`);
  if (v?.parentElement) v.parentElement.scrollTo({ left: v.offsetLeft - v.parentElement.offsetLeft });
};

// Its own component: a lineage landing re-renders the strip, never every pane.
function ViewGlances({ families }: { families: FamilyId[] }) {
  const { close } = useWorkspace();
  const figures = useViewedCourse()?.figures ?? [];
  return (
    <nav className="view-glances" aria-label="Open views">
      {families.map((family) => {
        const { label, glance } = FAMILIES[family];
        const row = figures.find((r) => r.key === glance);
        return (
          <span key={family} className="view-glance">
            <Button variant="ghost" onClick={() => show(family)}>
              <Said tone={row?.tone ?? "quiet"} label={label} value={row?.value ?? null} />
            </Button>
            <Button variant="ghost" aria-label={`Close ${label}`} onClick={() => close(family)}>
              <IconClose />
            </Button>
          </span>
        );
      })}
    </nav>
  );
}

// Every open view is a column and its own scroller, newest first: a phone swipes between them, a wide
// screen shows as many as fit. Past one, a strip reads each at a glance, and a tap scrolls to it.
export function Views() {
  const { views } = useWorkspace();
  const columns = views.filter((v) => FAMILIES[v.family].pane !== null).reverse();
  const newest = columns[0]?.family;
  // Snapping holds the column it was on, so a view just opened is brought into view.
  useEffect(() => void (newest && show(newest)), [newest]);
  return (
    <>
      {columns.length > 1 && <ViewGlances families={columns.map((v) => v.family)} />}
      <div className="views">
        {columns.map(({ family, key }) => {
          const pane = FAMILIES[family].pane!(key);
          return (
            <div key={family} id={`view-${family}`} className="view">
              {family === "chat" ? (
                pane
              ) : (
                <div className="content">
                  <div className="box-grid">{pane}</div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}
