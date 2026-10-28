"use client";

import { useCallback } from "react";
import { useSelection } from "@/lib/SelectionContext";
import { useWorkspace } from "@/lib/workspace";
import { isSelectedCandidate } from "@/lib/types";
import { selectedNodeOf } from "@/lib/derivations";
import { pathLeaf, pathOfAddress } from "@/lib/ids";
import type { HierarchyCandidate } from "@/lib/api";

interface SelectNode {
  isPicked: (node: HierarchyCandidate) => boolean;
  // A deselect returns to the candidate's own course.
  pick: (node: HierarchyCandidate) => void;
}

// `resume` is the campaign LIST's: a deselect landing on another campaign's root resumes it.
export function useSelectNode(opts: { resume?: boolean } = {}): SelectNode {
  const { resume = false } = opts;
  const { navigate } = useWorkspace();
  const { candidate, setSelectionForCandidate } = useSelection();

  const isPicked = useCallback(
    (node: HierarchyCandidate) => {
      const own = selectedNodeOf(node, pathLeaf(pathOfAddress(node.address)).cycleId);
      return isSelectedCandidate(candidate, own.cycle_id, own.round, own.arm);
    },
    [candidate],
  );

  const pick = useCallback(
    (node: HierarchyCandidate) => {
      const nodePath = pathOfAddress(node.address);
      const own = selectedNodeOf(node, pathLeaf(nodePath).cycleId);
      const selected = isSelectedCandidate(candidate, own.cycle_id, own.round, own.arm);
      if (selected) navigate(nodePath, { resume });
      else navigate(nodePath, { candidate: node.facts.individual });
      setSelectionForCandidate(selected ? null : own);
    },
    [candidate, navigate, resume, setSelectionForCandidate],
  );

  return { isPicked, pick };
}
