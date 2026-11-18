import { useCallback, useMemo } from "react";
import { candidateSubject } from "@/lib/api/reads";
import { useCompareSelection } from "@/lib/state/compare-selection";
import { armAt, originAt, originOf } from "@/lib/derivations";
import type { CandidatePick } from "@/lib/address";
import type { CyclePath } from "@/lib/ids";
import { useViewedLineage } from "@/lib/state/lineage";
import { useWorkspace } from "@/lib/state/workspace";

interface CompareWithOrigin {
  run: (() => void) | null;
  originLabel: string | null;
  /** ORIGIN first; one entry while the point IS the origin. Stable per pair, so it can key a read. */
  subjects: readonly string[];
}

const NO_SUBJECTS: readonly string[] = [];

export function useCompareWithOrigin(
  path: CyclePath | null,
  // A candidate at `path`; `null` names no point and reads the origin alone.
  pick: CandidatePick | null,
): CompareWithOrigin {
  const { show } = useCompareSelection();
  const { open } = useWorkspace();
  const { index } = useViewedLineage();
  // Primitives, so a pick rebuilt each render re-keys nothing.
  const round = pick?.round ?? null;
  const arm = pick?.arm ?? null;

  const pair = useMemo(() => {
    if (!path) return null;
    const placed = round === null || arm === null ? null : armAt(index, path, { round, arm });
    // A refused proposal measured no individual: there is no point to compare.
    const point = placed?.facts.reading.end === "invalid" ? null : placed;
    if (arm !== null && !point) return null;
    const origin = point ? originOf(index, point) : originAt(index, path);
    if (!origin) return null;
    const originKey = candidateSubject(origin.address);
    const pointKey = point ? candidateSubject(point.address) : "";
    const subjects = pointKey && pointKey !== originKey ? [originKey, pointKey] : [originKey];
    return { subjects, originLabel: origin.label };
  }, [index, path, round, arm]);

  const run = useCallback(() => {
    if (!pair) return;
    show(pair.subjects);
    open("compare");
  }, [pair, show, open]);

  return {
    run: pair ? run : null,
    originLabel: pair?.originLabel ?? null,
    subjects: pair?.subjects ?? NO_SUBJECTS,
  };
}
