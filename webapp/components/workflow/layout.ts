// Workflow topology — pipeline shape never changes (per docs/specs/archive/m11-webapp-minimal-preview.md).
// Lifted verbatim from webapp/index.html:1181 (LAYOUT) and :1199 (EDGES).
// Three nested loops:
//   L1 (innermost): l1_critique → l1_generate (bow under the row)
//   L2 (middle):    l1_critique → l2_context → l1_generate (rounded square)
//   L3 (outer):     l1_critique → l3_plan    → l1_generate (bigger rounded square)

export const CANVAS_W = 800;
export const CANVAS_H = 274;

export interface NodeBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export const LAYOUT: Record<string, NodeBox> = {
  input:       { x:  10, y:  50, w:  80, h: 30 },
  restructure: { x:  10, y: 100, w:  80, h: 44 },
  origin:    { x:  10, y: 170, w:  80, h: 44 },
  l3_plan:     { x: 265, y:  10, w: 110, h: 44 },
  l2_context:  { x: 265, y:  67, w: 110, h: 44 },
  l1_generate: { x: 160, y: 170, w: 110, h: 44 },
  l1_score:    { x: 290, y: 170, w:  70, h: 44 },
  l1_critique: { x: 380, y: 170, w: 100, h: 44 },
  output:      { x: 380, y: 230, w: 100, h: 40 },
};

export type EdgeKind = "forward" | "loop" | "directive" | "escalate";

export interface EdgeGeometry {
  kind: EdgeKind;
  d: string;
  label?: string;
  labelXY?: [number, number];
}

export const EDGES: Record<string, EdgeGeometry> = {
  "input>restructure":        { kind: "forward",   d: "M50,80 L50,100" },
  "restructure>origin":     { kind: "forward",   d: "M50,144 L50,170" },
  "origin>l1_generate":     { kind: "forward",   d: "M90,192 L160,192" },
  "l1_generate>l1_score":     { kind: "forward",   d: "M270,192 L290,192" },
  "l1_score>l1_critique":     { kind: "forward",   d: "M360,192 L380,192" },
  "l1_critique>output":       { kind: "forward",   d: "M430,214 L430,230",
                                label: "converged", labelXY: [460, 224] },
  "l1_critique>l1_generate":  { kind: "forward",
                                d: "M430,170 C430,140 215,140 215,170",
                                label: "next round", labelXY: [322, 144] },
  "l1_critique>l2_context":   { kind: "escalate",
                                d: "M445,170 Q445,89 375,89",
                                label: "L1 stall", labelXY: [432, 130] },
  "l2_context>l1_generate":   { kind: "directive",
                                d: "M265,89 Q200,89 200,170",
                                label: "brief",    labelXY: [215, 130] },
  "l1_critique>l3_plan":      { kind: "escalate",
                                d: "M470,170 Q470,33 375,33",
                                label: "L2 stall", labelXY: [458, 105] },
  "l3_plan>l1_generate":      { kind: "directive",
                                d: "M265,33 Q180,33 180,170",
                                label: "plan",     labelXY: [193, 105] },
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
