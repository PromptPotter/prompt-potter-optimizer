"use client";
import { useMemo } from "react";
import { liveCandidates, useCycleStream } from "@/lib/poll";
import type { ServedRound } from "@/lib/api/types";
import { useSelection } from "@/lib/SelectionContext";
import { barsAreCourses, candidateBars, type LineageIndex } from "@/lib/derivations";
import { encodeCyclePath } from "@/lib/ids";
import { useWorkspace } from "@/lib/workspace";
import { measuredUniverse } from "@/lib/sample-set";
import type { CandidateBar } from "@/lib/types";

export function useCandidateBars(index: LineageIndex) {
  const { dash } = useCycleStream();
  const { viewedPath, viewedCandidateId } = useWorkspace();
  const { sampleSet } = useSelection();

  const history: ServedRound[] = useMemo(() => dash?.rounds ?? [], [dash?.rounds]);

  const lineRoundNum = dash?.overlap_line_round ?? null;
  const overlap = useMemo(
    () => history.find((r) => r.round === lineRoundNum)?.overlap ?? null,
    [history, lineRoundNum],
  );

  const sampleUniverse = useMemo(() => measuredUniverse(history), [history]);

  const viewedNode = useMemo(() => {
    if (!viewedPath) return undefined;
    const entry = index.get(encodeCyclePath(viewedPath));
    if (!entry) return undefined;
    return viewedCandidateId
      ? entry.candidates.find((c) => c.id === viewedCandidateId)
      : (entry.course ?? undefined);
  }, [index, viewedPath, viewedCandidateId]);

  const inflightByLabel = useMemo(
    () => new Map(liveCandidates(dash).map((c) => [c.reading.arm.label, c.reading])),
    [dash],
  );

  const areCourses = useMemo(() => barsAreCourses(viewedNode), [viewedNode]);

  const benchPass = dash?.bench_pass ?? null;
  const views = useMemo<CandidateBar[]>(
    () =>
      candidateBars({
        viewedNode,
        inflightByLabel,
        sampleSet,
        benchPass,
      }),
    [viewedNode, inflightByLabel, sampleSet, benchPass],
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
