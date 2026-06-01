// Workflow topology — pipeline shape never changes (per docs/specs/archive/m11-webapp-minimal-preview.md).
// Dot-and-outside-label layout, matching the chat-pane hero aesthetic.
// Narrow/tall arrangement so the canvas fits a portrait phone:
//   feeder column (Dataset → checkin) stacks above l1_generate on the left;
//   the L1 row runs l1_generate → l1_score → l1_critique left-to-right;
//   the l2/l3 escalation nodes stack above l1_critique on the right;
//   directives (brief, plan) arc back through the centre gap to l1_generate;
//   the L1 loop bows under the row.
// `checkin` is the merged check-in + origin node — the origin-scoring phase
// highlights it (see phaseToNodeId).
//
// Edge endpoints sit at the dot edge (DOT_R from each centre), not the
// centre, so the stroke never overlaps the dot or its glow.

export const CANVAS_W = 360;
export const CANVAS_H = 300;
// Vertical variant for portrait phones — the same graph re-flowed top-to-
// bottom (the L1 chain runs down the page, l2/l3 branch to the right) so
// it reads on a tall narrow screen. Picked by WorkflowCanvas via
// useIsPortraitPhone(); the desktop set above is unchanged.
export const CANVAS_V_W = 340;
export const CANVAS_V_H = 470;
export const DOT_R = 11;

export interface NodePoint {
  cx: number;
  cy: number;
  // Optional label placement. Defaults reproduce the horizontal layout:
  // centred, below the dot. The vertical layout puts labels beside the dot
  // (anchor "start", offset right) so the top-to-bottom connectors never
  // run through the text.
  labelDx?: number;
  labelDy?: number;
  labelAnchor?: "start" | "middle" | "end";
}

export const LAYOUT: Record<string, NodePoint> = {
  input:       { cx:  96, cy:  40 },
  checkin:     { cx:  96, cy: 112 },
  l1_generate: { cx:  96, cy: 196 },
  l1_score:    { cx: 200, cy: 196 },
  l1_critique: { cx: 300, cy: 196 },
  l2_context:  { cx: 200, cy: 112 },
  l3_plan:     { cx: 200, cy:  40 },
  output:      { cx: 300, cy: 268 },
};

export type EdgeKind = "forward" | "loop" | "directive" | "escalate";

export interface EdgeGeometry {
  kind: EdgeKind;
  d: string;
  label?: string;
  labelXY?: [number, number];
}

export const EDGES: Record<string, EdgeGeometry> = {
  "input>checkin":            { kind: "forward",   d: "M96,51 L96,101" },
  "checkin>l1_generate":      { kind: "forward",   d: "M96,123 L96,185" },
  "l1_generate>l1_score":     { kind: "forward",   d: "M107,196 L189,196" },
  "l1_score>l1_critique":     { kind: "forward",   d: "M211,196 L289,196" },
  "l1_critique>output":       { kind: "forward",   d: "M300,207 L300,257" },
  "l1_critique>l1_generate":  { kind: "loop",
                                d: "M291,187 C238,165 162,165 105,187" },
  "l1_critique>l2_context":   { kind: "escalate",
                                d: "M290,190 Q250,150 211,116" },
  "l2_context>l1_generate":   { kind: "directive",
                                d: "M190,118 Q140,160 105,188",
                                label: "brief", labelXY: [128, 170] },
  "l1_critique>l3_plan":      { kind: "escalate",
                                d: "M297,186 Q280,70 211,44" },
  "l3_plan>l1_generate":      { kind: "directive",
                                d: "M192,46 Q150,150 104,186",
                                label: "plan", labelXY: [150, 90] },
};

// Labels sit to the RIGHT of the dot on the vertical chain so the
// downward connectors don't cross the text.
const SIDE = { labelDx: 18, labelDy: 4, labelAnchor: "start" as const };

export const LAYOUT_V: Record<string, NodePoint> = {
  input:       { cx: 60, cy:  34, ...SIDE },
  checkin:     { cx: 60, cy: 112, ...SIDE },
  l1_generate: { cx: 60, cy: 190, ...SIDE },
  l1_score:    { cx: 60, cy: 268, ...SIDE },
  l1_critique: { cx: 60, cy: 346, ...SIDE },
  output:      { cx: 60, cy: 424, ...SIDE },
  l3_plan:     { cx: 250, cy: 160 },
  l2_context:  { cx: 250, cy: 280 },
};

export const EDGES_V: Record<string, EdgeGeometry> = {
  "input>checkin":            { kind: "forward",   d: "M60,45 L60,101" },
  "checkin>l1_generate":      { kind: "forward",   d: "M60,123 L60,179" },
  "l1_generate>l1_score":     { kind: "forward",   d: "M60,201 L60,257" },
  "l1_score>l1_critique":     { kind: "forward",   d: "M60,279 L60,335" },
  "l1_critique>output":       { kind: "forward",   d: "M60,357 L60,413" },
  "l1_critique>l1_generate":  { kind: "loop",
                                d: "M50,352 C18,300 18,238 50,196" },
  "l1_critique>l2_context":   { kind: "escalate",
                                d: "M71,342 Q170,338 239,284" },
  "l2_context>l1_generate":   { kind: "directive",
                                d: "M239,274 Q150,235 71,194",
                                label: "brief", labelXY: [158, 250] },
  "l1_critique>l3_plan":      { kind: "escalate",
                                d: "M70,339 Q185,250 240,168" },
  "l3_plan>l1_generate":      { kind: "directive",
                                d: "M240,152 Q150,150 71,182",
                                label: "plan", labelXY: [156, 142] },
};

// dash.state → workflow node id
export function phaseToNodeId(state: string | null | undefined): string | null {
  if (!state) return null;
  const s = String(state).toLowerCase();
  // Origin scoring folds into the merged checkin node.
  if (s === "origin") return "checkin";
  if (s === "l1_generate") return "l1_generate";
  if (s === "l1_critique") return "l1_critique";
  if (s === "l2_refining") return "l2_context";
  if (s === "l3_replanning") return "l3_plan";
  if (s === "scoring" || s === "between_samples" || s === "between_candidates") return "l1_score";
  return null;
}
