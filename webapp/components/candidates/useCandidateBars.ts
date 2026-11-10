import { useMemo } from "react";
import { useViewedDashboard } from "@/lib/poll";
import type { ServedRound } from "@/lib/api/types";
import { useSelection } from "@/lib/state/selection";
import {
  barsAreCourses,
  candidateBars,
  candidatesAtPath,
  courseAt,
  liveCandidates,
  measuredUniverse,
  type LineageIndex,
} from "@/lib/derivations";
import { useWorkspace } from "@/lib/workspace";
import type { CandidateBar } from "@/lib/types";

export function useCandidateBars(index: LineageIndex) {
  const { dash } = useViewedDashboard();
  const { viewed, pick } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const pickedAddress = pick?.address ?? null;
  const { sampleSet } = useSelection();

  const history: ServedRound[] = useMemo(() => dash?.rounds ?? [], [dash?.rounds]);

  const lineRoundNum = dash?.overlap_line_round ?? null;
  const overlap = useMemo(
    () => history.find((r) => r.round === lineRoundNum)?.overlap ?? null,
    [history, lineRoundNum],
  );

  const sampleUniverse = useMemo(() => measuredUniverse(history), [history]);

  // A pick holding inner courses is drilled into: its bars are those courses.
  const viewedNode = useMemo(() => {
    if (!viewedPath) return undefined;
    const picked = candidatesAtPath(index, viewedPath).find((c) => c.address === pickedAddress);
    return picked && barsAreCourses(picked) ? picked : (courseAt(index, viewedPath) ?? undefined);
  }, [index, viewedPath, pickedAddress]);

  const inflightByKey = useMemo(
    () => new Map(liveCandidates(dash).map((c) => [c.reading.arm.key, c.reading])),
    [dash],
  );

  const areCourses = useMemo(() => barsAreCourses(viewedNode), [viewedNode]);

  const marks = dash?.verification;
  const views = useMemo<CandidateBar[]>(
    () =>
      candidateBars({
        viewedNode,
        inflightByKey,
        sampleSet,
        marks: marks ?? [],
      }),
    [viewedNode, inflightByKey, sampleSet, marks],
  );

  const floorPinned = useMemo(
    () =>
      views.filter((v) => v.reading?.ability?.caveat === "floor_pinned").map((v) => v.label),
    [views],
  );

  return {
    history,
    overlap,
    sampleUniverse,
    viewedNode,
    areCourses,
    views,
    floorPinned,
  };
}
