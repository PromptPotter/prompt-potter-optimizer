"use client";
import { memo, useCallback, useMemo } from "react";
import type { DashboardSnapshot } from "@/lib/poll";
import { rootCycleId, shortFamilyTail } from "@/lib/ids";
import { fmtPct0 } from "@/lib/format";
import { useStableContent } from "@/lib/stable";
import { CardFrame } from "@/components/ui/Card";
import { useSelection } from "./SelectionContext";
import { FamilyTree } from "./FamilyTree";
import { roundCandidatesByRound } from "@/lib/derivations/round-candidates";
import type { CandidateRow } from "@/lib/types/candidate";

interface Props {
  dash: DashboardSnapshot | null;
  campaignId: string | null;
  // The cycle currently in view. Used to recognise inherited forks: when
  // cycleId differs from rootCycleId(cycleId), the cycle is a sibling
  // (fork/diag/sweep) and its empty rounds[] doesn't mean "fresh" — it
  // means "no NEW rounds yet on top of inherited parent history."
  cycleId: string | null;
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

// One round-N branch: parent → child segment plus the child node's
// position. Pairing them in one shape lets the per-branch React.memo gate
// skip every prior round's SVG on selection change or new-round mount.
interface BranchSlot {
  key: string;
  row: CandidateRow;
  px: number;
  py: number;
  cx: number;
  cy: number;
  labelX: number;
}

const BranchNode = memo(function BranchNode({
  slot,
  isSelected,
  onPick,
}: {
  slot: BranchSlot;
  isSelected: boolean;
  onPick: (row: CandidateRow, isSelected: boolean) => void;
}) {
  return (
    <>
      <line
        x1={slot.px}
        y1={slot.py}
        x2={slot.cx}
        y2={slot.cy}
        className={`lineage-branch${slot.row.is_winner ? " winner" : ""}`}
      />
      <g
        className={`lineage-node${isSelected ? " selected" : ""}`}
        role="button"
        tabIndex={0}
        aria-pressed={isSelected}
        aria-label={`Round ${slot.row.round} candidate ${slot.row.label}, accuracy ${fmtPct0(slot.row.accuracy)}${slot.row.is_winner ? ", round winner" : ""}`}
        onClick={() => onPick(slot.row, isSelected)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onPick(slot.row, isSelected);
          }
        }}
        style={{ cursor: "pointer" }}
      >
        <line
          x1={slot.cx}
          y1={slot.cy}
          x2={slot.cx + STUB}
          y2={slot.cy}
          className={`lineage-stub${slot.row.is_winner ? " winner" : ""}`}
        />
        <text
          x={slot.labelX}
          y={slot.cy + 3}
          className={`lineage-label${slot.row.is_winner ? " winner" : ""}`}
        >
          {slot.row.label} {fmtPct0(slot.row.accuracy)}
        </text>
        {/* Invisible hit-rect over the label so the row catches clicks
            beyond just the thin stub. */}
        <rect
          x={slot.cx}
          y={slot.cy - 10}
          width={STUB + 110}
          height={20}
          fill="transparent"
        />
        <title>{slot.row.label}</title>
      </g>
    </>
  );
});

export const LineageTree = memo(function LineageTree({
  dash,
  campaignId,
  cycleId,
  onSelectCycle,
}: Props) {
  const { candidate: selected, setSelectionForCandidate } = useSelection();

  // One canonical candidate spine for the whole dashboard. Lineage
  // and FitnessPanel share this derivation so the two surfaces agree
  // on count, ordering, ids, and labels.
  const byRound = useMemo(() => roundCandidatesByRound(dash), [dash]);

  // L1 rounds in display order — round 0 is origin and rendered as the
  // trunk stub, not as a column. Everything ≥1 becomes a column.
  // useStableContent caches by content equality so the expensive layout
  // memo below re-runs only when the tree shape actually changes (new
  // round, new candidate, winner flip), not every poll.
  const l1Rounds = useStableContent(
    useMemo(() => {
      const nums = [...byRound.keys()].filter((r) => r > 0).sort((a, b) => a - b);
      return nums.map((round) => ({ round, rows: byRound.get(round) ?? [] }));
    }, [byRound]),
  );

  const originRow = byRound.get(0)?.[0] ?? null;

  // Walk rounds in order. Each round's children sit at column N (1-indexed),
  // vertically centered on the parent of round N (origin or prior winner).
  // Winner of round N becomes the parent point for round N+1.
  const { branches, height, totalW, originY } = useMemo(() => {
    const slots: BranchSlot[] = [];
    let parentX = LEFT_PAD;
    let parentY = TOP_PAD + 4 * ROW_H; // initial guess; recomputed per-round
    let maxY = parentY;
    l1Rounds.forEach((r, ri) => {
      const rows = r.rows;
      if (rows.length === 0) return;
      const colRight = LEFT_PAD + r.round * ROUND_W;
      const childStubX = colRight - STUB;
      const span = (rows.length - 1) * ROW_H;
      const top = Math.max(TOP_PAD, parentY - span / 2);
      const cys = rows.map((_, i) => top + i * ROW_H);
      const roundStart = slots.length;
      rows.forEach((row, i) => {
        const cy = cys[i];
        slots.push({
          key: row.key,
          row,
          px: parentX,
          py: parentY,
          cx: childStubX,
          cy,
          labelX: colRight + 4,
        });
        if (cy > maxY) maxY = cy;
      });
      // Re-center parent for round 1 (origin) onto its children so the
      // origin trunk aligns with the middle of the fan.
      if (ri === 0) {
        const mid = (cys[0] + cys[cys.length - 1]) / 2;
        parentY = mid;
        for (let k = roundStart; k < slots.length; k++) slots[k].py = mid;
      }
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
      branches: slots,
      height: maxY + TOP_PAD,
      totalW: LEFT_PAD + (l1Rounds[l1Rounds.length - 1]?.round ?? 1) * ROUND_W + 80,
      originY: l1Rounds.length > 0 ? (slots[0]?.py ?? TOP_PAD) : TOP_PAD,
    };
  }, [l1Rounds]);

  const onPickRow = useCallback(
    (row: CandidateRow, isSelected: boolean) => {
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
    },
    [setSelectionForCandidate],
  );

  const isRowSelected = (row: CandidateRow): boolean =>
    selected != null &&
    selected.round === row.round &&
    selected.candidate_id === row.candidate_id;

  if (l1Rounds.length === 0) {
    // Inherited fork: dashboard.json is shared at the family root, so
    // origin / round / best surface the parent's state. The fork's own
    // rounds dir is just empty because it hasn't run a new round yet —
    // calling that "waiting for round 1" misleads.
    const parentId = cycleId ? rootCycleId(cycleId) : null;
    const isInheritedSibling = parentId != null && parentId !== cycleId;
    const inheritedBest = dash?.best ?? null;
    return (
      <CardFrame
        className="lineage-card"
        title={<span>Lineage</span>}
        actions={<span className="badge">{isInheritedSibling ? "inherited" : "waiting"}</span>}
      >
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
  // just by clicking it.
  const originSelected = originRow ? isRowSelected(originRow) : false;

  return (
    <CardFrame
      className="lineage-card"
      title={<span>Lineage</span>}
      actions={
        <span className="badge">{branches.length} candidates · {l1Rounds.length} round{l1Rounds.length === 1 ? "" : "s"}</span>
      }
    >
      {/* Campaign cladogram sits above the candidate cladogram — same
          visual language, different scale (cross-cycle vs within-cycle).
          Self-hides for single-cycle campaigns. */}
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
          {originRow ? (
            <g
              className={`lineage-node lineage-node-origin${originSelected ? " selected" : ""}`}
              role="button"
              tabIndex={0}
              aria-pressed={originSelected}
              aria-label={`Origin ${originRow.label}, accuracy ${fmtPct0(originRow.accuracy)}`}
              onClick={() => onPickRow(originRow, originSelected)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onPickRow(originRow, originSelected);
                }
              }}
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

          {branches.map((slot) => (
            <BranchNode
              key={slot.key}
              slot={slot}
              isSelected={isRowSelected(slot.row)}
              onPick={onPickRow}
            />
          ))}
        </svg>
      </div>
    </CardFrame>
  );
});
