"use client";
// ONE gesture: pick a candidate node off the served tree — navigate to it, and inspect it.
//
// It has two callers and they are the two surfaces that hold a `LineageNode`: the sidebar
// tree (which is where navigating and inspecting have always been one gesture) and the
// time-ray (whose candidate steps land on the same address). The shape earns a home because
// of what it protects rather than because of how long it is:
//
//   • `SelectedCandidate` carries `accuracy` and `is_winner`, and `ScoringInspector` RENDERS
//     `is_winner`. Neither is derivable from a ray item, so the ray resolves the node off the
//     tree and copies the measurement from there. Every writer of this selection goes
//     through here so none of them can invent one — a fabricated measurement beside a live
//     bar is the failure this exists to prevent.
//   • DESELECTING returns to the course whose TIMELINE the node sits on, not to the node's
//     own path with a null candidate. For anything a course minted those are the same
//     address; for a FORK's contribution they are not — the server dissolves a fork onto its
//     parent's timeline, so `(forkPath, null)` names nothing and the bars come back empty.

import { useCallback } from "react";
import { useSelection } from "@/lib/SelectionContext";
import { isSelectedCandidate } from "@/lib/types";
import { pathOf, selectedCandidateOf } from "@/lib/derivations";
import { pathLeaf, type CyclePath } from "@/lib/ids";
import type { LineageNode } from "@/lib/api";

export interface SelectNode {
  /** True when this node is the one currently inspected. */
  isPicked: (node: LineageNode) => boolean;
  /**
   * Toggle the pick. `timeline` is the course whose row the node is rendered on — the
   * address a deselect returns to. Pass the node's own path when there is no separate
   * timeline (a caller that is already looking at the minting course).
   */
  pick: (node: LineageNode, timeline: CyclePath) => void;
}

export function useSelectNode(
  selectCyclePath: (path: CyclePath, candidateId?: string | null) => void,
): SelectNode {
  const { candidate, setSelectionForCandidate } = useSelection();

  const isPicked = useCallback(
    (node: LineageNode) =>
      isSelectedCandidate(
        candidate,
        pathLeaf(pathOf(node)).cycleId,
        node.round ?? 0,
        node.id,
      ),
    [candidate],
  );

  const pick = useCallback(
    (node: LineageNode, timeline: CyclePath) => {
      const nodePath = pathOf(node);
      const cycleId = pathLeaf(nodePath).cycleId;
      const selected = isSelectedCandidate(candidate, cycleId, node.round ?? 0, node.id);
      if (selected) selectCyclePath(timeline, null);
      else selectCyclePath(nodePath, node.id);
      setSelectionForCandidate(selected ? null : selectedCandidateOf(node, cycleId));
    },
    [candidate, selectCyclePath, setSelectionForCandidate],
  );

  return { isPicked, pick };
}
