// The one shade for a SERVED fitness, so one fitness reads one colour everywhere — never a grade
// of its own.

import type { CSSProperties } from "react";

export function fitnessStyle(fitness: number): CSSProperties {
  const hue = 5 + fitness * 125;
  const alpha = 0.18 + Math.abs(fitness - 0.5) * 0.4;
  return { background: `hsla(${hue},70%,45%,${alpha.toFixed(3)})` };
}
