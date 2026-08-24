// Workflow topology — the pipeline shape never changes.
// Dot-and-outside-label layout, matching the chat-pane hero aesthetic.
// One compact wide-short arrangement at every width, so the optimizer card stays
// short and the dashboard with it:
//   checkin sits ABOVE l1_generate, where the run enters the loop;
//   the L1 chain runs l1_generate → l1_score → l1_critique left-to-right along
//   the bottom row;
//   the l2/l3 escalation nodes sit on the top row beside checkin;
//   directives (brief, plan) arc back through the centre gap to l1_generate;
//   the L1 loop bows up over the row;
//   the produced search point leaves the right edge as a labelled arrow —
//   there is no output dot.
// `checkin` is the merged check-in + origin node — the origin-scoring phase
// highlights it (see activeNodeId).
//
// The three columns are pitched 130px apart. That pitch is the whole reason the
// canvas is this wide: each dot carries the node's resolved MODEL beneath its
// label (`WorkflowCanvas`'s `sub` line), and a provider-qualified model name is
// far wider than the node id above it. At the old 360/~78 pitch those names
// overlapped their neighbours' — the canvas is `overflow:hidden`, so they were
// clipped rather than scrolled, and the strip read as if the models were unset.
//
// Edge endpoints sit at the dot edge (DOT_R from each centre), not the
// centre, so the stroke never overlaps the dot or its glow.

export const CANVAS_W = 460;
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
// Every TOP-ROW node labels ABOVE its dot. Both escalate edges terminate on the
// BOTTOM edge of those dots (`M332,109 L330,51`), which is exactly where a default
// label band sits — so the arrowhead was drawn through the text and "l3_plan" read
// as "l3 an". Nothing sits above the top row, so the band moves there rather than
// the edges being re-routed. `checkin` joined that row and inherits the same rule.
const LABEL_ABOVE = -(DOT_R + 17);

export const LAYOUT: Record<string, NodePoint> = {
  checkin:     { cx:  70, cy:  40, labelDy: LABEL_ABOVE },
  l2_context:  { cx: 200, cy:  40, labelDy: LABEL_ABOVE },
  l3_plan:     { cx: 330, cy:  40, labelDy: LABEL_ABOVE },
  l1_generate: { cx:  70, cy: 120 },
  l1_score:    { cx: 200, cy: 120 },
  l1_critique: { cx: 330, cy: 120 },
};

// Ids that carry no LAYOUT entry ON PURPOSE. Anything else missing from LAYOUT is
// DRIFT — the served optimizer view gained a node this hand-drawn geometry does not
// know about — and WorkflowCanvas renders those in a stray row rather than dropping
// them, so the divergence is visible instead of silent. The two deliberate absences:
//   `output` — drawn as the terminal arrow leaving the right edge, not as a dot.
//   `input`  — the DATASET feeder, which is not part of the loop. It fed exactly one
//              edge into checkin and cost a whole column to say what the pipeline
//              stack already says (it is the level below `l1_score`), while this card
//              answers "what is the loop doing". Do not restore it: the node stays on
//              the served view, so its absence here is a placement decision, not a gap.
export const INTENTIONALLY_UNPLACED: ReadonlySet<string> = new Set(["output", "input"]);

export type EdgeKind = "forward" | "loop" | "directive" | "escalate";

export interface EdgeGeometry {
  kind: EdgeKind;
  d: string;
  label?: string;
  labelXY?: [number, number];
}

// No `input>checkin` entry. The served view still declares that edge; WorkflowCanvas
// looks each one up here and skips a miss, which is the same path that already let
// `output`'s dot be absent — so dropping the dataset feeder needs no second mechanism.
export const EDGES: Record<string, EdgeGeometry> = {
  "checkin>l1_generate":      { kind: "forward",   d: "M70,51 L70,109" },
  "l1_generate>l1_score":     { kind: "forward",   d: "M81,120 L189,120" },
  "l1_score>l1_critique":     { kind: "forward",   d: "M211,120 L319,120" },
  // Terminal edge — leaves the right edge; label ("Best SP") comes from
  // the output node, filled in by WorkflowCanvas, not hardcoded here.
  "l1_critique>output":       { kind: "forward",   d: "M341,120 L451,120",
                                labelXY: [396, 111] },
  "l1_critique>l1_generate":  { kind: "loop",
                                d: "M322,112 C250,80 150,80 78,112" },
  "l1_critique>l2_context":   { kind: "escalate",
                                d: "M323,113 Q262,78 210,50" },
  "l2_context>l1_generate":   { kind: "directive",
                                d: "M191,49 Q130,84 78,111",
                                label: "brief", labelXY: [125, 95] },
  "l1_critique>l3_plan":      { kind: "escalate",
                                d: "M332,109 L330,51" },
  "l3_plan>l1_generate":      { kind: "directive",
                                d: "M319,47 Q200,78 78,112",
                                label: "plan", labelXY: [232, 84] },
};

// "Which optimizer node is live right now" is SERVED — `dash.current_round.active_node`
// (`live_dashboard/view.py::_active_node`), over a map that is TOTAL across the state
// vocabulary. Derived here it covered three states, and every other one resolved to "nothing
// running": the canvas went dark between two optimizer calls and stayed dark for the whole
// `l2_context` reasoning call. Do not re-derive it.
