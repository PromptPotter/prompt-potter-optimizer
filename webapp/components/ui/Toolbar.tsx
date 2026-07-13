import type { ReactNode } from "react";
import { cx } from "@/lib/cx";
import s from "./Toolbar.module.css";

// A card's control bar — ONE row, always. Groups of related controls separated by
// hairlines, a spacer that pushes trailing actions right. The row never wraps:
// controls are icon-sized, so the density is the point.
//
// This exists because the alternative is what the candidates header became — a
// four-row pile of labelled buttons taller than some of the charts it controls.
// A toolbar is a fixed-height surface; if it can't fit, the answer is a denser
// control or a menu, never another row.
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx(s.bar, className)}>{children}</div>;
}

// A hairline between two groups of controls. Purely visual — it carries no
// meaning a screen reader needs, so it is hidden from the tree.
export function ToolbarSep() {
  return <span className={s.sep} aria-hidden="true" />;
}

// Eats the slack, pushing everything after it to the right edge.
export function ToolbarSpacer() {
  return <span className={s.spacer} aria-hidden="true" />;
}
