import type { LineageNode } from "@/lib/api/types";

// Twin of `domain/scoring.py::is_hit`, for a DISCRETE HIT/MISS word only — anything that shades,
// averages or ranks reads `fitness`. Never grow ERR/UNSC arms here; `status` is served.
export const HIT_THRESHOLD = 1.0;

export function isHit(fitness: number | null | undefined): boolean {
  return typeof fitness === "number" && fitness >= HIT_THRESHOLD;
}

export type LiftSide = NonNullable<LineageNode["reference_lift_side"]>;

// Twin of `domain/dashboard_rows.py::lift_side` off `spans`, ONLY for the one wire that carries
// an interval and no side: a round FILE's scoreboard row. Every other surface reads the served side.
export function liftSeparates(lo: number, hi: number): boolean {
  return lo > 0 || hi < 0;
}

// The one wording; a surface choosing its own drifts like choosing its own predicate.
export const NOT_SEPARABLE = "could not separate from its parent";
