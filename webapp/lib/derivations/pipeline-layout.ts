import type { PipelineViewEdge, PipelineViewNode } from "@/components/workflow";

// A looping pipeline as a two-row serpentine, read boustrophedon in running order. The order is
// read off the served graph — nothing placed by hand, nothing inferred from an id.

export interface PlacedNode {
  x: number;
  y: number;
  row: number;
  col: number;
  // Runs once, outside the repeating round: context for the loop, never a step of it.
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
  cell: number;
  /** Must clear a row's label AND the model under it. */
  rowH: number;
  padTop: number;
  padBottom: number;
}

/** [] when the graph does not loop. */
export function cycleOf(nodes: PipelineViewNode[], edges: PipelineViewEdge[]): string[] {
  const ids = new Set(nodes.map((n) => n.id));
  const loop = edges.find((e) => e.kind === "loop" && ids.has(e.from) && ids.has(e.to));
  if (!loop) return [];
  const forward = new Map<string, string>();
  for (const e of edges) {
    if (e.kind === "forward" && ids.has(e.from) && ids.has(e.to)) forward.set(e.from, e.to);
  }
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
  const cols = Math.max(Math.ceil(order.length / 2), 1);
  const rows = Math.max(Math.ceil(order.length / cols), 1);

  const pos = new Map<string, PlacedNode>();
  order.forEach((n, i) => {
    const row = Math.floor(i / cols);
    const along = i % cols;
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
