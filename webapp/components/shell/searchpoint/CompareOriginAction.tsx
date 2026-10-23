"use client";

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
  const { run, originLabel } = useCompareWithOrigin(path, candidate.arm);
  return (
    <button
      type="button"
      className="btn"
      onClick={run ?? undefined}
      disabled={run === null}
      title={
        run === null
          ? "Nothing to compare: a candidate still scoring is not placed on the lineage yet, and a refused proposal measured no individual."
          : "Open Compare on the origin and this searchpoint, replacing what is on the board."
      }
    >
      Compare {candidate.label} with {originLabel ?? "the origin"}
    </button>
  );
}
