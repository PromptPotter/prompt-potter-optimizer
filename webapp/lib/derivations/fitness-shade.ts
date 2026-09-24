// The one shade for a SERVED fitness in [0, 1] — red through green, stronger toward either
// end. A colour for a number the server already graded, never a grade of its own: every strip
// tile and every cell badge shades through here, so one fitness reads one colour everywhere.

import type { CSSProperties } from "react";

export function fitnessStyle(fitness: number): CSSProperties {
  const hue = 5 + fitness * 125;
  const alpha = 0.18 + Math.abs(fitness - 0.5) * 0.4;
  return { background: `hsla(${hue},70%,45%,${alpha.toFixed(3)})` };
}
