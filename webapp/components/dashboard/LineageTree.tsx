"use client";
import { useMemo } from "react";
import {
  liveL1Candidates,
  roundOf,
  useCycleStream,
  type DashboardSnapshot,
} from "@/lib/poll";
import { rootCycleId, shortFamilyTail } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { parseSampleLine } from "@/lib/sample-line";
import { useSelection } from "./SelectionContext";
import { FamilyTree } from "./FamilyTree";

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
  dash: DashboardSnapshot | null;
  // The campaign the viewed cycle belongs to — needed for the campaign
  // lineage fetch (FamilyTree) and any per-cycle resolution.
  campaignId: string | null;
  // The cycle currently in view. Used to recognise inherited forks: when
  // cycleId differs from rootCycleId(cycleId), the cycle is a sibling
  // (fork/diag/sweep) and its empty rounds[] doesn't mean "fresh" — it
  // means "no NEW rounds yet on top of inherited parent history."
  cycleId: string | null;
  // Re-select the parent unit when the operator clicks the inheritance
  // hint. A unit is the pair (campaignId, cycleId) — lineage is
  // campaign-scoped, so the parent shares this view's campaignId.
  onSelectCycle?: (campaignId: string, cycleId: string) => void;
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

export function LineageTree({ dash, campaignId, cycleId, onSelectCycle }: Props) {
  const { selected, setSelected } = useSelection();
  const { rounds: docs } = useCycleStream();
  // Same merge logic FitnessPanel uses: historical rounds from disk +
  // a partial "live" round when the dash carries L1 candidates that
  // haven't been written to round_NNNN.json yet. Without this the
  // lineage lags the fitness panel by a whole round (the operator sees
  // C4.x bars in the chart but no R4 column in the tree).
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
    const inflight = liveL1Candidates(dash);
    const currentRound = roundOf(dash);
    if (inflight.length > 0 && currentRound != null && !out.some((r) => r.round === currentRound)) {
      const scoreboard: Candidate[] = inflight.map((c) => {
        const i = Number(c.idx);
        let acc: number | null = null;
        if (typeof c.stats?.accuracy === "number") {
          acc = c.stats.accuracy;
        } else if (c.samples && c.samples.length > 0) {
          let hits = 0, total = 0;
          for (const raw of c.samples) {
            const p = typeof raw === "string" ? parseSampleLine(raw) : null;
            if (p?.status === "HIT") { hits += 1; total += 1; }
            else if (p?.status === "MISS") { total += 1; }
            else if (raw && typeof raw === "object" && typeof raw.hit === "boolean") {
              if (raw.hit) hits += 1;
              total += 1;
            }
          }
          acc = total > 0 ? hits / total : null;
        }
        return {
          candidate_id: `r${currentRound}_${i}`,
          label: c.label,
          accuracy: acc ?? undefined,
          is_winner: false,
        };
      });
      out.push({ round: currentRound, scoreboard });
    }
    return out;
  }, [docs, dash]);

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
      // Right padding sized for the final column's label text ("R{N}.{i} {pct}%")
      // — ~80px is enough; the prior 140 left a wide empty strip on the right.
      totalW: LEFT_PAD + (rounds[rounds.length - 1]?.round ?? 1) * ROUND_W + 80,
      originY: rounds.length > 0 ? (segs[0]?.py ?? TOP_PAD) : TOP_PAD,
    };
  }, [rounds]);

  // Live origin if available — surfaces before the first round file lands.
  // Prefer ``origin`` over ``origin_accuracy``: both are the same scalar at the
  // source, but legacy backend writes left ``origin_accuracy`` at 0.0 while
  // populating ``origin`` correctly. ``typeof === number`` would otherwise
  // accept the 0.0 and render "origin 0%" against a real 50% campaign.
  const originDash =
    (dash as { origin?: number } | null)?.origin ?? dash?.origin_accuracy ?? null;
  const originAcc = originDash ?? rounds[0]?.origin_accuracy ?? null;

  if (rounds.length === 0) {
    // Inherited fork: dashboard.json is shared at the family root, so
    // origin / round / best surface the parent's state. The fork's own
    // rounds dir is just empty because it hasn't run a new round yet —
    // calling that "waiting for round 1" misleads. Detect via id shape
    // (matches rootCycleId() in Python paths.py) and re-frame.
    const parentId = cycleId ? rootCycleId(cycleId) : null;
    const isInheritedSibling = parentId != null && parentId !== cycleId;
    const inheritedBest =
      (dash as { best_accuracy?: number } | null)?.best_accuracy ??
      (dash as { best?: number } | null)?.best ??
      null;
    return (
      <div className="card lineage-card">
        <div className="card-title">
          <span>Lineage</span>
          <span className="badge">{isInheritedSibling ? "inherited" : "waiting"}</span>
        </div>
        {/* Even when this cycle has no candidate rounds yet, the family
            cladogram (root + descendants) is still informative — operator
            can scan + click siblings. Self-hides when the family has no
            descendants. */}
        <FamilyTree
          campaignId={campaignId}
          cycleId={cycleId}
          onSelectCycle={onSelectCycle ?? (() => {})}
        />
        <div className="lineage-empty">
          {isInheritedSibling && parentId ? (
            <>
              inherited from{" "}
              {onSelectCycle && campaignId ? (
                <button
                  type="button"
                  className="lineage-inherit-link"
                  onClick={() => onSelectCycle(campaignId, parentId)}
                  title={`Switch to ${parentId}`}
                >
                  {shortFamilyTail(parentId) || parentId}
                </button>
              ) : (
                <span>{shortFamilyTail(parentId) || parentId}</span>
              )}
              {inheritedBest != null ? ` · best ${fmtPct0(inheritedBest)}` : ""}
              {" · no new rounds yet"}
            </>
          ) : originAcc != null ? (
            `origin ${fmtPct0(originAcc)} · waiting for round 1`
          ) : (
            "No rounds on disk yet — the tree appears once round 1 lands."
          )}
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
      {/* Campaign cladogram sits above the candidate cladogram — same
          visual language, different scale (cross-cycle vs within-cycle).
          Self-hides for single-cycle campaigns so it stays out of the way. */}
      <FamilyTree
        campaignId={campaignId}
        cycleId={cycleId}
        onSelectCycle={onSelectCycle ?? (() => {})}
      />
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
            origin {fmtPct0(originAcc)}
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

          {/* Horizontal child stubs + labels. Clickable: selecting a node
             feeds the ScoringInspector embedded below within this card. */}
          {branches.children.map((c) => {
            const cid = c.cand.candidate_id ?? `r${c.round}_${c.idx}`;
            const isSelected =
              selected != null && selected.round === c.round && selected.candidate_id === cid;
            return (
              <g
                key={`child-${c.round}-${c.idx}`}
                className={`lineage-node${isSelected ? " selected" : ""}`}
                onClick={() => {
                  if (isSelected) {
                    setSelected(null);
                  } else {
                    setSelected({
                      round: c.round,
                      candidate_id: cid,
                      label: c.cand.label ?? cid,
                      accuracy: typeof c.cand.accuracy === "number" ? c.cand.accuracy : null,
                      is_winner: !!c.cand.is_winner,
                    });
                  }
                }}
                style={{ cursor: "pointer" }}
              >
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
                  R{c.round}.{c.idx + 1} {fmtPct0(c.cand.accuracy)}
                </text>
                {/* Invisible hit-rect over the label so the row catches clicks
                   beyond just the thin stub. */}
                <rect
                  x={c.x}
                  y={c.y - 10}
                  width={STUB + 110}
                  height={20}
                  fill="transparent"
                />
                <title>{c.cand.label ?? c.cand.candidate_id ?? ""}</title>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
