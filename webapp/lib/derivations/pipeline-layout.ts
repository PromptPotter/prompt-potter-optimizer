import type { PipelineViewEdge, PipelineViewNode } from "@/lib/api/types";

interface Pt {
  x: number;
  y: number;
}

interface PlacedNode extends Pt {
  row: number;
  col: number;
  // Runs once, outside the repeating round: context for the loop, never a step of it.
  muted: boolean;
  // The id, broken after a `_` only where it would not fit its cell.
  label: string[];
  // The model, broken after its provider's `/` the same way.
  sub: string[];
}

interface RoutedEdge {
  from: string;
  to: string;
  kind: PipelineViewEdge["kind"];
  d: string;
  muted: boolean;
}

interface GridLayout {
  pos: Map<string, PlacedNode>;
  edges: RoutedEdge[];
  width: number;
  height: number;
  cols: number;
  rows: number;
  cell: number;
  // From a dot's centre to the top of its first text line; each line is one `lineH` tall.
  textTop: number;
  lineH: number;
}

interface GridOpts {
  // The label font's glyph advance and line pitch; `null` draws no text (a compact level).
  font: { charW: number; lineH: number } | null;
  // The text under a node beside its id: its model, or "".
  sub: (id: string) => string;
  // A dot's diameter.
  dot: number;
  // Clear space between two cells' text, and an outer route lane's width.
  gutter: number;
  // The box it folds into; `Infinity` never folds.
  maxW: number;
}

export function flowOrder(nodes: PipelineViewNode[], cycle: string[]): PipelineViewNode[] {
  const onSpine = new Set(cycle);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const entry = nodes
    .filter((n) => n.tier === 0 && !onSpine.has(n.id))
    .sort((a, b) => a.rank - b.rank);
  const spine = cycle.map((id) => byId.get(id)).filter((n): n is PipelineViewNode => !!n);
  const alternatives = nodes
    .filter((n) => n.tier > 0)
    .sort((a, b) => a.tier - b.tier || a.rank - b.rank);
  return [...entry, ...spine, ...alternatives];
}

// Each piece keeps its separator, so the lines rejoin into the name.
function pieces(text: string, sep: string): string[] {
  const parts = text.split(sep);
  return parts.map((p, i) => (i < parts.length - 1 ? p + sep : p)).filter((p) => p !== "");
}

// Greedy: a line takes the next piece while it stays within `chars`; a longer piece stands alone.
export function wrapAt(text: string, sep: string, chars: number): string[] {
  const lines: string[] = [];
  for (const piece of pieces(text, sep)) {
    const tail = lines.at(-1);
    if (tail !== undefined && tail.length + piece.length <= chars) {
      lines[lines.length - 1] = tail + piece;
    } else {
      lines.push(piece);
    }
  }
  return lines;
}

// Along one row: a step to the next cell sags a hair; one back, or past a cell, bows up by `rise` at most.
export function rowHopD(a: Pt, b: Pt, r: number, rise: number): string {
  const s = b.x >= a.x ? 1 : -1;
  const x1 = a.x + s * r;
  const x2 = b.x - s * r;
  const mx = (x1 + x2) / 2;
  const bow = rise > 0 ? -Math.min(Math.abs(b.x - a.x) * 0.16, rise) : 6;
  return `M ${x1} ${a.y} C ${mx} ${a.y + bow} ${mx} ${a.y + bow} ${x2} ${a.y}`;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

export function layoutGrid(
  nodes: PipelineViewNode[],
  edges: PipelineViewEdge[],
  cycle: string[],
  o: GridOpts,
): GridLayout {
  const order = flowOrder(nodes, cycle);
  const onSpine = new Set(cycle);
  const index = new Map(order.map((n, i) => [n.id, i] as const));
  const font = o.font;
  const texts = order.map((n) => ({ n, sub: font ? o.sub(n.id) : "" }));
  const widest = (lens: number[]) => Math.max(0, ...lens);
  // Every line broken at every separator, and none broken: the narrowest and widest text a cell holds.
  const minChars = widest(
    texts.flatMap((t) => [...pieces(t.n.id, "_"), ...pieces(t.sub, "/")].map((p) => p.length)),
  );
  const openChars = widest(texts.flatMap((t) => [t.n.id.length, t.sub.length]));
  const cellOf = (chars: number) => (font ? Math.max(chars * font.charW, o.dot) : o.dot) + o.gutter;

  // The serpentine's two rows, folded into more where the box holds fewer columns.
  const wanted = Math.max(Math.ceil(order.length / 2), 1);
  const fold = (lane: number) => clamp(Math.floor((o.maxW - lane) / cellOf(minChars)), 1, wanted);
  const rowOf = (i: number, cols: number) => Math.floor(i / cols);
  const spansRows = (cols: number) =>
    edges.some((e) => {
      const a = index.get(e.from);
      const b = index.get(e.to);
      return a !== undefined && b !== undefined && Math.abs(rowOf(a, cols) - rowOf(b, cols)) > 1;
    });
  let cols = fold(0);
  const laned = spansRows(cols);
  if (laned) cols = fold(o.gutter);
  const rows = Math.max(Math.ceil(order.length / cols), 1);

  // A line breaks only where the fold leaves it no room, so no unbroken line sets the cell.
  const avail = o.maxW - (laned ? o.gutter : 0);
  const chars = font
    ? clamp(Math.floor((avail / cols - o.gutter) / font.charW), minChars, openChars)
    : 0;
  const grid = texts.map(({ n, sub }, i) => {
    const row = rowOf(i, cols);
    const along = i % cols;
    const label = font ? wrapAt(n.id, "_", chars) : [];
    const subLines = font ? wrapAt(sub, "/", chars) : [];
    return { n, row, col: row % 2 === 0 ? along : cols - 1 - along, label, sub: subLines };
  });
  const cell = cellOf(widest(grid.flatMap((g) => [...g.label, ...g.sub].map((s) => s.length))));

  const r = o.dot / 2;
  const lineH = font ? font.lineH : 0;
  const below = r + widest(grid.map((g) => g.label.length + g.sub.length)) * lineH;
  // A dot's width over each centre clears its lock glyph; rows sit a dot apart, room for a route.
  const above = o.dot;
  const gap = o.dot;
  const rowH = above + below + gap;
  const rowY = (row: number) => above + row * rowH;
  const cellAt = new Map(grid.map((g) => [g.n.id, g] as const));

  // A route spanning rows runs down the outer lane on the side its ends lean to.
  const sideOf = (e: PipelineViewEdge): "left" | "right" | null => {
    const a = cellAt.get(e.from);
    const b = cellAt.get(e.to);
    if (!a || !b || Math.abs(a.row - b.row) < 2) return null;
    return (a.col + b.col) / 2 < (cols - 1) / 2 ? "left" : "right";
  };
  const sides = new Set(edges.map(sideOf));
  const leftLane = sides.has("left") ? o.gutter : 0;
  const rightLane = sides.has("right") ? o.gutter : 0;

  const pos = new Map<string, PlacedNode>();
  for (const { n, row, col, label, sub } of grid) {
    pos.set(n.id, {
      x: leftLane + cell * (col + 0.5),
      y: rowY(row),
      row,
      col,
      muted: n.tier === 0 && !onSpine.has(n.id),
      label,
      sub,
    });
  }
  const width = leftLane + cell * cols + rightLane;
  // The middle of the clear band over a row: a route's run between rows.
  const gapMid = (row: number) => rowY(row) - above - gap / 2;

  const routed: RoutedEdge[] = [];
  for (const e of edges) {
    const a = pos.get(e.from);
    const b = pos.get(e.to);
    if (!a || !b) continue;
    const rise = (above + gap) * 0.78;
    let d: string;
    if (a.row === b.row) {
      d = rowHopD(a, b, r, b.x < a.x || Math.abs(a.col - b.col) > 1 ? rise : 0);
    } else if (Math.abs(a.row - b.row) === 1) {
      // Across the band between one row's text and the next row's dots: it touches neither row.
      const start = a.row < b.row ? { x: a.x, y: a.y + below } : { x: a.x, y: a.y - r };
      const end = a.row < b.row ? { x: b.x, y: b.y - r } : { x: b.x, y: b.y + below };
      d = `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
    } else {
      const laneX = sideOf(e) === "left" ? leftLane / 2 : width - rightLane / 2;
      const up = b.row < a.row;
      const out = up ? gapMid(a.row) : gapMid(a.row + 1);
      const into = up ? gapMid(b.row + 1) : gapMid(b.row);
      const pts: [number, number][] = [
        [a.x, up ? a.y - r : a.y + below],
        [a.x, out],
        [laneX, out],
        [laneX, into],
        [b.x, into],
        [b.x, up ? b.y + below : b.y - r],
      ];
      d = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
    }
    routed.push({ from: e.from, to: e.to, kind: e.kind, d, muted: a.muted || b.muted });
  }

  return {
    pos,
    edges: routed,
    width,
    height: above + (rows - 1) * rowH + below,
    cols,
    rows,
    cell,
    textTop: r,
    lineH,
  };
}
