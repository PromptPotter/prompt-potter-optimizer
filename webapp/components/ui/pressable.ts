import type { KeyboardEvent } from "react";

// The native button activation for an element that cannot BE a `<button>` — an SVG `<g>`,
// a row hosting a focusable descendant. Spread it onto the element beside its `aria-label`.
export function pressable(onActivate: () => void) {
  return {
    role: "button" as const,
    tabIndex: 0,
    onClick: () => onActivate(),
    onKeyDown: (e: KeyboardEvent<Element>) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      onActivate();
    },
  };
}
