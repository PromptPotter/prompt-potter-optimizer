import { CAP_AXIS_INFO, type CapAxis, type CapAxisInfo } from "@/lib/api/types.generated";

export interface CapBounds {
  integer?: boolean | undefined;
  minimum?: number | null | undefined;
  maximum?: number | null | undefined;
  exclusive_minimum?: number | null | undefined;
}

// Blank names no cap; REFUSED (malformed or out of bounds) is neither, so the typed text stays to fix.
export type Cap = { value: number } | { blank: true } | { refused: true };

// An omitted `minimum` floors at 0; a null one is no floor.
function bounded(n: number, { integer = false, minimum = 0, maximum, exclusive_minimum }: CapBounds): Cap {
  const out =
    !Number.isFinite(n) ||
    (integer && !Number.isInteger(n)) ||
    (minimum != null && n < minimum) ||
    (maximum != null && n > maximum) ||
    (exclusive_minimum != null && n <= exclusive_minimum);
  return out ? { refused: true } : { value: n };
}

export function parseCap(raw: string, bounds: CapBounds = {}): Cap {
  const s = raw.trim();
  return s === "" ? { blank: true } : bounded(Number(s), bounds);
}

export function isCapAxis(key: string): key is CapAxis {
  return key in CAP_AXIS_INFO;
}

// The wire value of what an operator typed in the axis's unit, bounded on the wire.
export function parseCapIn(raw: string, spec: CapBounds & { scale: number }): Cap {
  const typed = parseCap(raw, { minimum: null });
  return "value" in typed ? bounded(typed.value * spec.scale, spec) : typed;
}

// A cap's wire value as an operator types it: in the axis's unit.
export function capText(axis: CapAxisInfo, value: number): string {
  return `${value / axis.scale} ${axis.unit}`;
}

// An axis's input bounds in its unit; an exclusive floor sits at `min` too, and `parseCap` refuses it.
export function capInput(axis: CapAxisInfo) {
  const floor = axis.minimum ?? axis.exclusive_minimum;
  return {
    type: "number",
    min: floor === null ? undefined : floor / axis.scale,
    step: axis.step,
    inputMode: axis.integer ? "numeric" : "decimal",
  } as const;
}
