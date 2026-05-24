// Workflow topology — pipeline shape never changes (per docs/specs/archive/m11-webapp-minimal-preview.md).
// Dot-and-outside-label layout, matching the chat-pane hero aesthetic.
// Three nested loops:
//   L1 (innermost): l1_critique → l1_generate (bow under the row)
//   L2 (middle):    l1_critique → l2_context → l1_generate (rounded square)
//   L3 (outer):     l1_critique → l3_plan    → l1_generate (bigger rounded square)
//
// Edge endpoints sit at the dot edge (DOT_R from each centre), not the
// centre, so the stroke never overlaps the dot or its glow.

export const CANVAS_W = 500;
export const CANVAS_H = 290;
export const DOT_R = 11;

export interface NodePoint {
  cx: number;
  cy: number;
}

export const LAYOUT: Record<string, NodePoint> = {
  input:       { cx:  50, cy:  60 },
  checkin:     { cx:  50, cy: 120 },
  origin:      { cx:  50, cy: 192 },
  l3_plan:     { cx: 290, cy:  32 },
  l2_context:  { cx: 290, cy:  89 },
  l1_generate: { cx: 185, cy: 192 },
  l1_score:    { cx: 295, cy: 192 },
  l1_critique: { cx: 400, cy: 192 },
  output:      { cx: 400, cy: 255 },
};

export type EdgeKind = "forward" | "loop" | "directive" | "escalate";

export interface EdgeGeometry {
  kind: EdgeKind;
  d: string;
  label?: string;
  labelXY?: [number, number];
}

export const EDGES: Record<string, EdgeGeometry> = {
  "input>checkin":            { kind: "forward",   d: "M50,71 L50,109" },
  "checkin>origin":           { kind: "forward",   d: "M50,131 L50,181" },
  "origin>l1_generate":       { kind: "forward",   d: "M61,192 L174,192" },
  "l1_generate>l1_score":     { kind: "forward",   d: "M196,192 L284,192" },
  "l1_score>l1_critique":     { kind: "forward",   d: "M306,192 L389,192" },
  "l1_critique>output":       { kind: "forward",   d: "M400,203 L400,244",
                                label: "converged", labelXY: [435, 226] },
  "l1_critique>l1_generate":  { kind: "loop",
                                d: "M400,181 C400,132 185,132 185,181",
                                label: "next round", labelXY: [292, 130] },
  "l1_critique>l2_context":   { kind: "escalate",
                                d: "M411,188 Q420,89 301,89",
                                label: "stall — refine", labelXY: [400, 130] },
  "l2_context>l1_generate":   { kind: "directive",
                                d: "M279,89 Q170,89 170,182",
                                label: "brief", labelXY: [185, 130] },
  "l1_critique>l3_plan":      { kind: "escalate",
                                d: "M411,184 Q448,32 301,32",
                                label: "stall — replan", labelXY: [438, 100] },
  "l3_plan>l1_generate":      { kind: "directive",
                                d: "M279,32 Q140,32 140,182",
                                label: "plan", labelXY: [155, 100] },
};

// dash.state → workflow node id
export function phaseToNodeId(state: string | null | undefined): string | null {
  if (!state) return null;
  const s = String(state).toLowerCase();
  if (s === "origin") return "origin";
  if (s === "l1_generate") return "l1_generate";
  if (s === "l1_critique") return "l1_critique";
  if (s === "l2_refining") return "l2_context";
  if (s === "l3_replanning") return "l3_plan";
  if (s === "scoring" || s === "between_samples" || s === "between_candidates") return "l1_score";
  return null;
}
