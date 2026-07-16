// Workflow topology — pipeline shape never changes (per docs/specs/archive/m11-webapp-minimal-preview.md).
// Dot-and-outside-label layout, matching the chat-pane hero aesthetic.
// One compact wide-short arrangement at every width (it renders inside a
// 360px-capped canvas on desktop too), so the optimizer card stays short
// and the dashboard with it:
//   the Dataset feeder stacks above checkin on the left;
//   the L1 chain runs checkin → l1_generate → l1_score → l1_critique left-
//   to-right along the bottom row;
//   the l2/l3 escalation nodes sit side-by-side on the top row;
//   directives (brief, plan) arc back through the centre gap to l1_generate;
//   the L1 loop bows up over the row;
//   the produced search point leaves the right edge as a labelled arrow —
//   there is no output dot.
// `checkin` is the merged check-in + origin node — the origin-scoring phase
// highlights it (see activeNodeId).
//
// Edge endpoints sit at the dot edge (DOT_R from each centre), not the
// centre, so the stroke never overlaps the dot or its glow.

export const CANVAS_W = 360;
export const CANVAS_H = 160;
export const DOT_R = 11;

export interface NodePoint {
  cx: number;
  cy: number;
  // Optional label placement; defaults are centred, below the dot.
  labelDx?: number;
  labelDy?: number;
  labelAnchor?: "start" | "middle" | "end";
}

// No `output` entry: the produced search point is drawn as a terminal
// arrow leaving the right edge (labelled from the output node's own
// label, "Best SP"), not as a dot. A node id absent from this map is
// skipped by WorkflowCanvas, so the wire data can still carry `output`.
export const LAYOUT: Record<string, NodePoint> = {
  input:       { cx:  52, cy:  40 },
  l2_context:  { cx: 210, cy:  40 },
  l3_plan:     { cx: 286, cy:  40 },
  checkin:     { cx:  52, cy: 120 },
  l1_generate: { cx: 132, cy: 120 },
  l1_score:    { cx: 210, cy: 120 },
  l1_critique: { cx: 286, cy: 120 },
};

export type EdgeKind = "forward" | "loop" | "directive" | "escalate";

export interface EdgeGeometry {
  kind: EdgeKind;
  d: string;
  label?: string;
  labelXY?: [number, number];
}

export const EDGES: Record<string, EdgeGeometry> = {
  "input>checkin":            { kind: "forward",   d: "M52,51 L52,109" },
  "checkin>l1_generate":      { kind: "forward",   d: "M63,120 L121,120" },
  "l1_generate>l1_score":     { kind: "forward",   d: "M143,120 L199,120" },
  "l1_score>l1_critique":     { kind: "forward",   d: "M221,120 L275,120" },
  // Terminal edge — leaves the right edge; label ("Best SP") comes from
  // the output node, filled in by WorkflowCanvas, not hardcoded here.
  "l1_critique>output":       { kind: "forward",   d: "M297,120 L351,120",
                                labelXY: [324, 111] },
  "l1_critique>l1_generate":  { kind: "loop",
                                d: "M278,112 C228,90 186,90 140,112" },
  "l1_critique>l2_context":   { kind: "escalate",
                                d: "M279,113 Q238,76 216,50" },
  "l2_context>l1_generate":   { kind: "directive",
                                d: "M203,49 Q158,86 138,111",
                                label: "brief", labelXY: [150, 98] },
  "l1_critique>l3_plan":      { kind: "escalate",
                                d: "M288,109 L286,51" },
  "l3_plan>l1_generate":      { kind: "directive",
                                d: "M279,47 Q200,82 140,112",
                                label: "plan", labelXY: [244, 100] },
};

// The single authoritative "which optimizer node is live right now" id — the
// one signal every surface (canvas pulse, RemoteBar, TopStrip, node detail)
// reads. The in-flight optimizer LLM call names its node directly
// (`l1_generate` / `l1_critique` / `l2_context` / `l3_plan` / `checkin`), so
// every LLM-call node lights up reliably — including critique and the L2/L3
// escalation nodes that the old `dash.state` enum never reached (no writer ever
// emitted `state="l1_critique"`). Scoring fires no optimizer LLM call, so the
// scoring states map to `l1_score`; the pre-scoring origin state → `checkin`.
export function activeNodeId(
  inFlightNode: string | null | undefined,
  state: string | null | undefined,
): string | null {
  if (inFlightNode) return inFlightNode;
  const s = state ? String(state).toLowerCase() : "";
  if (s === "scoring" || s === "between_samples" || s === "between_candidates") return "l1_score";
  // Origin scoring folds into the merged checkin node (pre-scoring window only).
  if (s === "origin") return "checkin";
  return null;
}
