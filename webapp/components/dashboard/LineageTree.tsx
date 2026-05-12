"use client";
import { useMemo } from "react";
import type { DashboardSnapshot } from "@/lib/poll";
import { useRoundHistory } from "@/lib/use-round-history";

interface Candidate {
  candidate_id?: string;
  label?: string;
  accuracy?: number;
  is_winner?: boolean;
}

interface RoundView {
  round: number;
  origin_accuracy?: number;
  scoreboard?: Candidate[];
}

interface Props {
  cycleId: string | null;
  refreshKey: number;
  dash: DashboardSnapshot | null;
}

// Minimal cladogram layout. Each round occupies a fixed column. Parent
// (origin for round 1, prior winner thereafter) sits at the column's left
// edge, vertically centered on its children. A slanted line runs from
// parent to the start of each child's horizontal stub; the stub carries
// the label.
const ROUND_W = 90;
const ROW_H = 22;
const STUB = 24;       // child horizontal length, before the label
const LEFT_PAD = 16;
const TOP_PAD = 20;

interface ChildPos {
  round: number;
  idx: number;
  cand: Candidate;
  x: number;           // start of child's horizontal stub
  y: number;
  labelX: number;
}

function fmtPct(v: number | undefined | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

export function LineageTree({ cycleId, refreshKey, dash }: Props) {
  const docs = useRoundHistory(cycleId, refreshKey);
  const rounds: RoundView[] = useMemo(() => {
    const out: RoundView[] = [];
    for (const d of docs) {
      if (typeof d.round !== "number") continue;
      out.push({
        round: d.round,
        origin_accuracy: d.origin_accuracy,
        scoreboard: d.scoreboard as Candidate[] | undefined,
      });
    }
    return out;
  }, [docs]);

  // Walk rounds in order. Each round's children sit at column N (1-indexed),
  // vertically centered on the parent of round N (origin or prior winner).
  // Winner of round N becomes the parent point for round N+1.
  const { branches, height, totalW, originY } = useMemo(() => {
    const children: ChildPos[] = [];
    type Branch = { px: number; py: number; cx: number; cy: number; winner: boolean };
    const segs: Branch[] = [];
    let parentX = LEFT_PAD;
    let parentY = TOP_PAD + 4 * ROW_H; // initial guess; recomputed per-round
    let maxY = parentY;
    rounds.forEach((r, ri) => {
      const cands = r.scoreboard ?? [];
      if (cands.length === 0) return;
      const colRight = LEFT_PAD + r.round * ROUND_W;
      const childStubX = colRight - STUB;
      // Center children vertically on parentY when possible.
      const span = (cands.length - 1) * ROW_H;
      const top = Math.max(TOP_PAD, parentY - span / 2);
      const cys = cands.map((_, i) => top + i * ROW_H);
      cands.forEach((c, i) => {
        const cy = cys[i];
        children.push({ round: r.round, idx: i, cand: c, x: childStubX, y: cy, labelX: colRight + 4 });
        segs.push({ px: parentX, py: parentY, cx: childStubX, cy, winner: !!c.is_winner });
        if (cy > maxY) maxY = cy;
      });
      // Re-center parent for round 1 (origin) onto its children so the
      // origin trunk aligns with the middle of the fan.
      if (ri === 0) {
        const mid = (cys[0] + cys[cys.length - 1]) / 2;
        parentY = mid;
        for (const s of segs) s.py = mid;
      }
      // Next parent = winner of this round
      const winnerIdx = cands.findIndex((c) => c.is_winner);
      if (winnerIdx >= 0) {
        parentX = colRight;
        parentY = cys[winnerIdx];
      } else {
        // No winner declared (round still in progress) — peg parent to the
        // top candidate so the next column can still render.
        parentX = colRight;
        parentY = cys[0];
      }
    });
    return {
      branches: { children, segs },
      height: maxY + TOP_PAD,
      totalW: LEFT_PAD + (rounds[rounds.length - 1]?.round ?? 1) * ROUND_W + 140,
      originY: rounds.length > 0 ? (segs[0]?.py ?? TOP_PAD) : TOP_PAD,
    };
  }, [rounds]);

  // Live origin if available — surfaces before the first round file lands.
  const originAcc = dash?.origin_accuracy ?? rounds[0]?.origin_accuracy ?? null;

  if (rounds.length === 0) {
    return (
      <div className="card lineage-card">
        <div className="card-title">
          <span>Lineage</span>
          <span className="badge">waiting</span>
        </div>
        <div className="lineage-empty">
          {originAcc != null
            ? `origin ${fmtPct(originAcc)} · waiting for round 1`
            : "No rounds on disk yet — the tree appears once round 1 lands."}
        </div>
      </div>
    );
  }

  return (
    <div className="card lineage-card">
      <div className="card-title">
        <span>Lineage</span>
        <span className="badge">{branches.children.length} candidates · {rounds.length} round{rounds.length === 1 ? "" : "s"}</span>
      </div>
      <div className="lineage-scroll">
        <svg
          width={totalW}
          height={height}
          viewBox={`0 0 ${totalW} ${height}`}
          xmlns="http://www.w3.org/2000/svg"
          className="lineage-svg"
          aria-label="Search-point lineage tree"
          shapeRendering="crispEdges"
        >
          {/* Origin label, anchored at the trunk's left tip. */}
          <text
            x={LEFT_PAD - 4}
            y={originY - 4}
            className="lineage-label"
            textAnchor="end"
          >
            origin {fmtPct(originAcc)}
          </text>

          {/* Slanted branches from parent point to each child's stub start. */}
          {branches.segs.map((s, i) => (
            <line
              key={`seg-${i}`}
              x1={s.px}
              y1={s.py}
              x2={s.cx}
              y2={s.cy}
              className={`lineage-branch${s.winner ? " winner" : ""}`}
            />
          ))}

          {/* Horizontal child stubs + labels. */}
          {branches.children.map((c) => (
            <g key={`child-${c.round}-${c.idx}`}>
              <line
                x1={c.x}
                y1={c.y}
                x2={c.x + STUB}
                y2={c.y}
                className={`lineage-stub${c.cand.is_winner ? " winner" : ""}`}
              />
              <text
                x={c.labelX}
                y={c.y + 3}
                className={`lineage-label${c.cand.is_winner ? " winner" : ""}`}
              >
                R{c.round}.{c.idx + 1} {fmtPct(c.cand.accuracy)}
              </text>
              <title>{c.cand.label ?? c.cand.candidate_id ?? ""}</title>
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}
