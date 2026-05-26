"use client";
import { memo, useMemo } from "react";
import type { DashboardSnapshot } from "@/lib/poll";
import { rootCycleId, shortFamilyTail } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { CardFrame } from "@/components/ui/card";
import { useSelection } from "./SelectionContext";
import { FamilyTree } from "./FamilyTree";
import { roundCandidatesByRound } from "@/lib/derivations/round-candidates";
import type { CandidateRow } from "@/lib/types/candidate";

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
  row: CandidateRow;
  x: number;           // start of child's horizontal stub
  y: number;
  labelX: number;
}

export const LineageTree = memo(function LineageTree({
  dash,
  campaignId,
  cycleId,
  onSelectCycle,
}: Props) {
  const { candidate: selected, setSelectionForCandidate } = useSelection();

  // One canonical candidate spine for the whole dashboard. Lineage
  // and FitnessPanel share this derivation so the two surfaces agree
  // on count, ordering, ids, and labels — no more R1.2-vs-C1.1 drift.
  const byRound = useMemo(() => roundCandidatesByRound(dash), [dash]);

  // L1 rounds in display order — round 0 is origin and rendered as the
  // trunk stub, not as a column. Everything ≥1 becomes a column.
  const l1Rounds = useMemo(() => {
    const nums = [...byRound.keys()].filter((r) => r > 0).sort((a, b) => a - b);
    return nums.map((round) => ({ round, rows: byRound.get(round) ?? [] }));
  }, [byRound]);

  const originRow = byRound.get(0)?.[0] ?? null;

  // Structural fingerprint of `l1Rounds` — captures round + per-row idx +
  // winner flag, the only `row` fields the layout below reads. Accuracy /
  // composite updates within a round bypass this key, so the heavy
  // segment-mutation loop only re-runs when the tree shape actually
  // changes (new round, new candidate, winner flip).
  const l1RoundsKey = useMemo(
    () =>
      l1Rounds
        .map(
          (r) =>
            `${r.round}:${r.rows
              .map((row) => `${row.idx}${row.is_winner ? "*" : ""}`)
              .join(",")}`,
        )
        .join("|"),
    [l1Rounds],
  );

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
    l1Rounds.forEach((r, ri) => {
      const rows = r.rows;
      if (rows.length === 0) return;
      const colRight = LEFT_PAD + r.round * ROUND_W;
      const childStubX = colRight - STUB;
      // Center children vertically on parentY when possible.
      const span = (rows.length - 1) * ROW_H;
      const top = Math.max(TOP_PAD, parentY - span / 2);
      const cys = rows.map((_, i) => top + i * ROW_H);
      rows.forEach((row, i) => {
        const cy = cys[i];
        children.push({ row, x: childStubX, y: cy, labelX: colRight + 4 });
        segs.push({ px: parentX, py: parentY, cx: childStubX, cy, winner: row.is_winner });
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
      const winnerIdx = rows.findIndex((row) => row.is_winner);
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
      totalW: LEFT_PAD + (l1Rounds[l1Rounds.length - 1]?.round ?? 1) * ROUND_W + 80,
      originY: l1Rounds.length > 0 ? (segs[0]?.py ?? TOP_PAD) : TOP_PAD,
    };
    // Heavy layout — keyed on the structural fingerprint, not `l1Rounds`
    // identity, so in-round accuracy updates don't re-trigger it. The closure
    // still reads the latest `l1Rounds` (captured per render) for the actual
    // row objects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [l1RoundsKey]);

  // Pure render: select a row when not selected, deselect when re-clicked.
  // Routed through SelectionContext.setSelectionForCandidate so the round
  // axis updates atomically — round-tabs strip + samples view follow.
  const onPickRow = (row: CandidateRow, isSelected: boolean) => {
    if (isSelected) {
      setSelectionForCandidate(null);
      return;
    }
    setSelectionForCandidate({
      round: row.round,
      candidate_id: row.candidate_id,
      label: row.label,
      accuracy: row.accuracy,
      is_winner: row.is_winner,
    });
  };

  const isRowSelected = (row: CandidateRow): boolean =>
    selected != null &&
    selected.round === row.round &&
    selected.candidate_id === row.candidate_id;

  if (l1Rounds.length === 0) {
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
      <CardFrame
        className="lineage-card"
        title={<span>Lineage</span>}
        actions={<span className="badge">{isInheritedSibling ? "inherited" : "waiting"}</span>}
      >
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
          ) : originRow?.accuracy != null ? (
            `origin ${fmtPct0(originRow.accuracy)} · waiting for round 1`
          ) : (
            "No rounds on disk yet — the tree appears once round 1 lands."
          )}
        </div>
      </CardFrame>
    );
  }

  // Clickable origin (C0) — same selection contract as any L1 candidate
  // so the operator can route to the inspector + samples view for origin
  // just by clicking it. Was a non-interactive text label before;
  // matches the fitness chart, which always treated C0 as a bar.
  const originSelected = originRow ? isRowSelected(originRow) : false;

  return (
    <CardFrame
      className="lineage-card"
      title={<span>Lineage</span>}
      actions={
        <span className="badge">{branches.children.length} candidates · {l1Rounds.length} round{l1Rounds.length === 1 ? "" : "s"}</span>
      }
    >
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
          {/* Origin label — clickable when an origin row exists in
              the candidate spine. Selecting it routes the inspector
              + samples view to C0 just like any other stub. */}
          {originRow ? (
            <g
              className={`lineage-node lineage-node-origin${originSelected ? " selected" : ""}`}
              onClick={() => onPickRow(originRow, originSelected)}
              style={{ cursor: "pointer" }}
            >
              <text
                x={LEFT_PAD - 4}
                y={originY - 4}
                className={`lineage-label${originSelected ? " selected" : ""}`}
                textAnchor="end"
              >
                {originRow.label} {fmtPct0(originRow.accuracy)}
              </text>
              <rect
                x={0}
                y={originY - 14}
                width={LEFT_PAD}
                height={20}
                fill="transparent"
              />
            </g>
          ) : null}

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
            const isSelected = isRowSelected(c.row);
            return (
              <g
                key={c.row.key}
                className={`lineage-node${isSelected ? " selected" : ""}`}
                onClick={() => onPickRow(c.row, isSelected)}
                style={{ cursor: "pointer" }}
              >
                <line
                  x1={c.x}
                  y1={c.y}
                  x2={c.x + STUB}
                  y2={c.y}
                  className={`lineage-stub${c.row.is_winner ? " winner" : ""}`}
                />
                <text
                  x={c.labelX}
                  y={c.y + 3}
                  className={`lineage-label${c.row.is_winner ? " winner" : ""}`}
                >
                  {c.row.label} {fmtPct0(c.row.accuracy)}
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
                <title>{c.row.label}</title>
              </g>
            );
          })}
        </svg>
      </div>
    </CardFrame>
  );
});
