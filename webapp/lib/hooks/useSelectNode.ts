"use client";
// ONE gesture: pick a served tree node — navigate to it and inspect it. Every writer of a node
// selection goes through here, so none can invent its `accuracy` / `is_winner`.

import { useCallback } from "react";
import { useSelection } from "@/lib/SelectionContext";
import { isSelectedCandidate } from "@/lib/types";
import { pathOf, selectedCandidateOf } from "@/lib/derivations";
import { pathLeaf, type CyclePath } from "@/lib/ids";
import type { LineageNode } from "@/lib/api";

export interface SelectNode {
  isPicked: (node: LineageNode) => boolean;
  /**
   * `timeline` is where a deselect returns — never the node's own path for a FORK, which the
   * server dissolves onto its parent's timeline, so `(forkPath, null)` names nothing.
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
