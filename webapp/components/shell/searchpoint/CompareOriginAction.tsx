"use client";
// The affordance for reading a searchpoint against the campaign's origin: both on the Compare
// board, nothing else beside them.

import type { SelectedCandidate } from "@/lib/types";
import type { CyclePath } from "@/lib/ids";
import { useCompareWithOrigin } from "@/lib/hooks/useCompareWithOrigin";

export function CompareOriginAction({
  candidate,
  path,
}: {
  candidate: SelectedCandidate;
  path: CyclePath | null;
}) {
  const { run, originLabel } = useCompareWithOrigin(path, candidate.candidate_id);
  return (
    <button
      type="button"
      className="btn"
      onClick={run ?? undefined}
      disabled={run === null}
      title={
        run === null
          ? "Not placed on the lineage yet: a candidate still scoring has no address to compare."
          : "Open Compare on the origin and this searchpoint, replacing what is on the board."
      }
    >
      Compare {candidate.label} with {originLabel ?? "the origin"}
    </button>
  );
}
