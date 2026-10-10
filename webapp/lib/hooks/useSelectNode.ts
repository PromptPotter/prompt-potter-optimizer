"use client";

import { useCallback } from "react";
import { useSelection } from "@/lib/SelectionContext";
import { useWorkspace } from "@/lib/workspace";
import { isSelectedCandidate } from "@/lib/types";
import { pathOf, selectedNodeOf } from "@/lib/derivations";
import { pathLeaf, type CyclePath } from "@/lib/ids";
import type { LineageNode } from "@/lib/api";

export interface SelectNode {
  isPicked: (node: LineageNode) => boolean;
  // A deselect returns to `timeline`, never a FORK's own path: `(forkPath, null)` names nothing.
  pick: (node: LineageNode, timeline: CyclePath) => void;
}

// `resume` is the campaign LIST's: a deselect landing on another campaign's root resumes it.
export function useSelectNode(opts: { resume?: boolean } = {}): SelectNode {
  const { resume = false } = opts;
  const { navigate } = useWorkspace();
  const { candidate, setSelectionForCandidate } = useSelection();

  const isPicked = useCallback(
    (node: LineageNode) => {
      const own = selectedNodeOf(node, pathLeaf(pathOf(node)).cycleId);
      return isSelectedCandidate(candidate, own.cycle_id, own.round, own.candidate_id);
    },
    [candidate],
  );

  const pick = useCallback(
    (node: LineageNode, timeline: CyclePath) => {
      const nodePath = pathOf(node);
      const own = selectedNodeOf(node, pathLeaf(nodePath).cycleId);
      const selected = isSelectedCandidate(candidate, own.cycle_id, own.round, own.candidate_id);
      if (selected) navigate(timeline, { resume });
      else navigate(nodePath, { candidate: node.id });
      setSelectionForCandidate(selected ? null : own);
    },
    [candidate, navigate, resume, setSelectionForCandidate],
  );

  return { isPicked, pick };
}
