import type { PipelineViewEdge, PipelineViewNode } from "@/components/workflow";

// Where a pipeline's nodes SIT, for a pipeline that loops.
//
// A chain is a rail and needs none of this — the renderer keeps that path. A graph whose
// steps REPEAT is laid out as a SERPENTINE over two rows:
//
//   checkin  →  l1_generate  →  l1_score          row 0, left to right
//                                    ↓
//   l3_plan  ←  l2_context   ←  l1_critique       row 1, right to left
//
// which is simply the flow folded in half. Read the rows boustrophedon — across, down,
// back — and you get the running order: the round, then what it escalates to when the
// round stalls. Nothing crosses the middle, every edge is one hop, and the return to
// `l1_generate` is a short lift rather than a sweep under the whole width.
//
// The order is READ off the served graph: what runs before the loop, then the loop in
// flow order, then each escalation by depth. Nothing is placed by hand and nothing is
// inferred from an id.

export interface PlacedNode {
  x: number;
  y: number;
  row: number;
  col: number;
  // Runs once, outside the repeating round. Drawn receded: it is context for the loop,
  // never a step of it.
  muted: boolean;
}

export interface GridLayout {
  pos: Map<string, PlacedNode>;
  width: number;
  height: number;
  cols: number;
  rows: number;
}

export interface GridOpts {
  /** Horizontal pitch — one column. */
  cell: number;
  /** Vertical pitch. Must clear a row's label AND the model under it. */
  rowH: number;
  /** Room above the first row's dots. */
  padTop: number;
  /** Room below the last row's dots, for its label and model. */
  padBottom: number;
}

/** The cycle's members in flow order, or [] when the graph does not loop. */
export function cycleOf(nodes: PipelineViewNode[], edges: PipelineViewEdge[]): string[] {
  const ids = new Set(nodes.map((n) => n.id));
  const loop = edges.find((e) => e.kind === "loop" && ids.has(e.from) && ids.has(e.to));
  if (!loop) return [];
  const forward = new Map<string, string>();
  for (const e of edges) {
    if (e.kind === "forward" && ids.has(e.from) && ids.has(e.to)) forward.set(e.from, e.to);
  }
  // Walk the forward chain from where the loop RETURNS to, until it reaches where the loop
  // leaves from. Bounded by the node count, so a malformed graph cannot spin.
  const out: string[] = [loop.to];
  let at = loop.to;
  while (at !== loop.from && out.length <= ids.size) {
    const next = forward.get(at);
    if (!next) return [];
    out.push(next);
    at = next;
  }
  return at === loop.from ? out : [];
}

/**
 * The running order: what runs ONCE before the loop, then the loop itself, then each
 * escalation by the depth you must reach to enter it.
 */
export function flowOrder(nodes: PipelineViewNode[], cycle: string[]): PipelineViewNode[] {
  const onSpine = new Set(cycle);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const entry = nodes
    .filter((n) => n.tier === 0 && !onSpine.has(n.id))
    .sort((a, b) => a.rank - b.rank);
  const spine = cycle.map((id) => byId.get(id)).filter((n): n is PipelineViewNode => !!n);
  const escalations = nodes
    .filter((n) => n.tier > 0)
    .sort((a, b) => a.tier - b.tier || a.rank - b.rank);
  return [...entry, ...spine, ...escalations];
}

export function layoutGrid(
  nodes: PipelineViewNode[],
  cycle: string[],
  o: GridOpts,
): GridLayout {
  const order = flowOrder(nodes, cycle);
  const onSpine = new Set(cycle);
  // Two rows: the card is a wide strip, so folding once is what keeps the run legible
  // without either axis running away. An odd count leaves the last row one short.
  const cols = Math.max(Math.ceil(order.length / 2), 1);
  const rows = Math.max(Math.ceil(order.length / cols), 1);

  const pos = new Map<string, PlacedNode>();
  order.forEach((n, i) => {
    const row = Math.floor(i / cols);
    const along = i % cols;
    // Serpentine: even rows run left-to-right, odd rows fold back. That fold is what puts
    // each escalation under the step it belongs beside instead of across the diagram.
    const col = row % 2 === 0 ? along : cols - 1 - along;
    pos.set(n.id, {
      x: o.cell * (col + 0.5),
      y: o.padTop + row * o.rowH,
      row,
      col,
      muted: n.tier === 0 && !onSpine.has(n.id),
    });
  });

  return {
    pos,
    width: o.cell * cols,
    height: o.padTop + (rows - 1) * o.rowH + o.padBottom,
    cols,
    rows,
  };
}
