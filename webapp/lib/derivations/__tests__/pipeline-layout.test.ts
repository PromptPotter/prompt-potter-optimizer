import { describe, expect, it } from "vitest";
import { flowOrder, layoutGrid, wrapAt } from "../pipeline-layout";
import type { PipelineView, PipelineViewNode } from "@/lib/types";

// What `derive_pipeline_view` serves for `assets/optimizers/potter/pipeline.yaml`: the `run_init`
// phase ahead of the `default` chain, which the loop closes.
const POTTER: PipelineView = (() => {
  const phase = ["block_pool", "demo_pool"];
  const chain = [
    "adaptive_queue",
    "l1_generate",
    "mating",
    "locus_crossover",
    "pobb",
    "l1_score",
    "theta_election",
    "l1_critique",
    "escalation",
  ];
  const kind = (id: string): PipelineViewNode["kind"] =>
    id === "l1_score" ? "measurement" : id.startsWith("l1_") ? "llm" : "member";
  const spine = [...phase, ...chain];
  const steps = ["input", ...spine, "output"];
  return {
    nodes: [
      { id: "input", description: "", kind: "io", tier: 0, rank: -1 },
      ...spine.map((id, rank) => ({ id, description: "", kind: kind(id), tier: 0, rank })),
      { id: "output", description: "", kind: "io", tier: 0, rank: spine.length },
    ],
    edges: [
      ...steps.slice(1).map((to, i) => ({ from: steps[i]!, to, kind: "forward" as const })),
      { from: "escalation", to: "adaptive_queue", kind: "loop" as const },
    ],
    cycle: chain,
  };
})();
const INTERIOR = POTTER.nodes.filter((n) => n.kind !== "io");

const FONT = { charW: 6.6, lineH: 11 };
const DOT = 14;
const MODEL = "minimax/minimax-m3.1-flash";
const SUBS: Record<string, (id: string) => string> = {
  "no models": () => "",
  models: (id) => (id === "l1_generate" || id === "l1_critique" ? MODEL : ""),
};

const lay = (maxW: number, sub: (id: string) => string) =>
  layoutGrid(INTERIOR, POTTER.edges, POTTER.cycle, { font: FONT, sub, dot: DOT, gutter: 14, maxW });

interface Box {
  id: string;
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}

// The dot, and each text line in the band under it: what a renderer draws for one node.
function boxesOf(out: ReturnType<typeof lay>): Box[] {
  return [...out.pos].flatMap(([id, p]) => {
    const r = DOT / 2;
    const lines = [...p.label, ...p.sub];
    return [
      { id, x0: p.x - r, x1: p.x + r, y0: p.y - r, y1: p.y + r },
      ...lines.map((line, i) => {
        const w = line.length * FONT.charW;
        const y0 = p.y + out.textTop + i * out.lineH;
        return { id, x0: p.x - w / 2, x1: p.x + w / 2, y0, y1: y0 + out.lineH };
      }),
    ];
  });
}

// Points along an `M` / `L` / `C` path, the only commands the layout emits.
function sample(d: string): [number, number][] {
  const tokens = d.trim().split(/\s+/);
  const pts: [number, number][] = [];
  let at: [number, number] = [0, 0];
  const num = (i: number) => Number(tokens[i]);
  for (let i = 0; i < tokens.length; ) {
    const cmd = tokens[i];
    if (cmd === "M") {
      at = [num(i + 1), num(i + 2)];
      pts.push(at);
      i += 3;
    } else if (cmd === "L") {
      const to: [number, number] = [num(i + 1), num(i + 2)];
      for (let t = 1; t <= 40; t++) {
        pts.push([at[0] + ((to[0] - at[0]) * t) / 40, at[1] + ((to[1] - at[1]) * t) / 40]);
      }
      at = to;
      i += 3;
    } else if (cmd === "C") {
      const c1: [number, number] = [num(i + 1), num(i + 2)];
      const c2: [number, number] = [num(i + 3), num(i + 4)];
      const to: [number, number] = [num(i + 5), num(i + 6)];
      for (let t = 1; t <= 40; t++) {
        const s = t / 40;
        const k = [(1 - s) ** 3, 3 * (1 - s) ** 2 * s, 3 * (1 - s) * s ** 2, s ** 3] as const;
        pts.push([
          k[0] * at[0] + k[1] * c1[0] + k[2] * c2[0] + k[3] * to[0],
          k[0] * at[1] + k[1] * c1[1] + k[2] * c2[1] + k[3] * to[1],
        ]);
      }
      at = to;
      i += 7;
    } else {
      throw new Error(`unexpected path command ${cmd}`);
    }
  }
  return pts;
}

const inside = (b: Box, [x, y]: [number, number]) =>
  x > b.x0 + 0.5 && x < b.x1 - 0.5 && y > b.y0 + 0.5 && y < b.y1 - 0.5;

const overlap = (a: Box, b: Box) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

describe("wrapAt", () => {
  it("breaks after a separator only where the line would not fit, and rejoins into the name", () => {
    expect(wrapAt("theta_election", "_", 20)).toEqual(["theta_election"]);
    expect(wrapAt("theta_election", "_", 10)).toEqual(["theta_", "election"]);
    expect(wrapAt("l1_critique_self_optimizing", "_", 16)).toEqual(["l1_critique_", "self_optimizing"]);
    expect(wrapAt(MODEL, "/", 20).join("")).toBe(MODEL);
    expect(wrapAt("", "/", 20)).toEqual([]);
  });
});

describe("flowOrder", () => {
  it("runs the phase that runs once, then the round it loops", () => {
    expect(flowOrder(INTERIOR, POTTER.cycle).map((n) => n.id)).toEqual([
      "block_pool",
      "demo_pool",
      ...POTTER.cycle,
    ]);
  });
});

describe("layoutGrid on the served potter view", () => {
  it("places every node once, receding only what runs once", () => {
    const out = lay(Infinity, SUBS.models!);
    expect([...out.pos.keys()].sort()).toEqual(INTERIOR.map((n) => n.id).sort());
    for (const [id, p] of out.pos) {
      expect(p.muted, id).toBe(id === "block_pool" || id === "demo_pool");
    }
    expect(out.rows).toBe(2);
  });

  it("holds its text, folds into its box, and routes no edge through a node", () => {
    const faults: string[] = [];
    for (const [name, sub] of Object.entries(SUBS)) {
      const folds = new Set<number>();
      for (let maxW = 80; maxW <= 1000; maxW += 10) {
        const out = lay(maxW, sub);
        const at = `${name} @ ${maxW}px, ${out.cols} cols`;
        folds.add(out.cols);
        const boxes = boxesOf(out);
        const spot = (b: Box) =>
          `${b.id} [${b.x0.toFixed(1)},${b.y0.toFixed(1)} ${b.x1.toFixed(1)},${b.y1.toFixed(1)}]`;

        for (const b of boxes) {
          if (b.x0 < 0) faults.push(`left ${spot(b)}, ${at}`);
          if (b.x1 > out.width) faults.push(`right ${spot(b)} past ${out.width}, ${at}`);
          if (b.y0 < 0) faults.push(`top ${spot(b)}, ${at}`);
          if (b.y1 > out.height) faults.push(`bottom ${spot(b)} past ${out.height}, ${at}`);
        }
        if (maxW >= out.cell + 14 && out.width > maxW) {
          faults.push(`width ${out.width} past ${maxW}, ${at}`);
        }

        for (const a of boxes) {
          for (const b of boxes) {
            if (a.id === b.id || out.pos.get(a.id)!.row !== out.pos.get(b.id)!.row) continue;
            if (overlap(a, b)) faults.push(`${spot(a)} meets ${spot(b)}, ${at}`);
          }
        }

        const present = out.edges.map((e) => `${e.from}>${e.to}`);
        if (!present.includes("escalation>adaptive_queue")) {
          faults.push(`loop edge missing among [${present.join(", ")}], ${at}`);
        }
        for (const e of out.edges) {
          const pts = sample(e.d);
          if (pts.some(([x, y]) => !Number.isFinite(x) || !Number.isFinite(y))) {
            faults.push(`${e.from}>${e.to} has a non-finite coordinate in "${e.d}", ${at}`);
            continue;
          }
          for (const b of boxes) {
            if (b.id === e.from || b.id === e.to) continue;
            const hit = pts.find((p) => inside(b, p));
            if (hit) {
              faults.push(
                `${e.from}>${e.to} crosses ${spot(b)} at ${hit[0].toFixed(1)},${hit[1].toFixed(1)}, ${at}`,
              );
            }
          }
        }

        for (const [id, p] of out.pos) {
          if (p.label.join("") !== id) faults.push(`${id} label "${p.label.join("")}", ${at}`);
          if (p.sub.join("") !== sub(id)) faults.push(`${id} sub "${p.sub.join("")}", ${at}`);
        }
      }
      for (const cols of [1, 2, 3, 4, 5, 6]) {
        if (!folds.has(cols)) faults.push(`${name} never folds to ${cols}`);
      }
    }
    expect(faults.slice(0, 30), `${faults.length} faults`).toEqual([]);
  });

  it("draws no text on a compact level, a dot per cell", () => {
    const out = layoutGrid(INTERIOR, POTTER.edges, POTTER.cycle, {
      font: null,
      sub: SUBS.models!,
      dot: 11,
      gutter: 33,
      maxW: Infinity,
    });
    expect(out.cell).toBe(44);
    for (const p of out.pos.values()) expect([...p.label, ...p.sub]).toEqual([]);
  });
});
