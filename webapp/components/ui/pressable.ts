import type { KeyboardEvent } from "react";

// For an element that cannot BE a `<button>`: an SVG `<g>`, a row hosting a focusable descendant.
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
