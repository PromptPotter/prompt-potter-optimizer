import { useCallback } from "react";
import { useWorkspace } from "@/lib/state/workspace";
import { pickOf } from "@/lib/derivations";
import { pathOfAddress } from "@/lib/ids";
import type { HierarchyCandidate } from "@/lib/api/types";

interface SelectNode {
  isPicked: (node: HierarchyCandidate) => boolean;
  // A deselect returns to the candidate's own course.
  pick: (node: HierarchyCandidate) => void;
}

// `resume` is the campaign LIST's: a deselect landing on another campaign's root resumes it.
export function useSelectNode(opts: { resume?: boolean } = {}): SelectNode {
  const { resume = false } = opts;
  const { navigate, pick: picked, pickCandidate } = useWorkspace();
  const pickedAddress = picked?.address ?? null;

  const isPicked = useCallback(
    (node: HierarchyCandidate) => node.address === pickedAddress,
    [pickedAddress],
  );

  const pick = useCallback(
    (node: HierarchyCandidate) => {
      const nodePath = pathOfAddress(node.address);
      if (node.address === pickedAddress) navigate({ path: nodePath }, { resume });
      else pickCandidate(pickOf(node), nodePath);
    },
    [pickedAddress, navigate, resume, pickCandidate],
  );

  return { isPicked, pick };
}
