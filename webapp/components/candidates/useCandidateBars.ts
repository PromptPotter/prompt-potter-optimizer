import { useMemo } from "react";
import { useViewedDashboard } from "@/lib/state/poll";
import type { LineArm, ServedRound } from "@/lib/api/types";
import { useSelection } from "@/lib/state/selection";
import {
  barsAreCycles,
  candidateBars,
  candidatesAtPath,
  cycleAt,
  type LineageIndex,
} from "@/lib/derivations";
import { useWorkspace } from "@/lib/state/workspace";
import { useEveryCandidate } from "@/lib/prefs/candidates";
import type { CandidateBar } from "@/lib/types";

const NO_LINE: LineArm[] = [];

export function useCandidateBars(index: LineageIndex) {
  const { dash } = useViewedDashboard();
  const { viewed, pick } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const pickedAddress = pick?.address ?? null;
  const { sampleSet } = useSelection();

  const history: ServedRound[] = useMemo(() => dash?.rounds ?? [], [dash?.rounds]);

  const lineRoundNum = dash?.overlap_line_round ?? null;
  const overlap = useMemo(
    () => history.find((r) => r.round === lineRoundNum)?.panel_reading ?? null,
    [history, lineRoundNum],
  );

  const coverage = dash?.sample_coverage ?? null;
  const sampleUniverse = useMemo(() => coverage?.samples.map((s) => s.sample_id) ?? [], [coverage]);

  // A pick holding inner cycles is drilled into: its bars are those cycles.
  const viewedNode = useMemo(() => {
    if (!viewedPath) return undefined;
    const picked = candidatesAtPath(index, viewedPath).find((c) => c.address === pickedAddress);
    return picked && barsAreCycles(picked) ? picked : (cycleAt(index, viewedPath) ?? undefined);
  }, [index, viewedPath, pickedAddress]);

  const line = dash?.line ?? NO_LINE;
  const areCycles = useMemo(() => barsAreCycles(viewedNode), [viewedNode]);

  const marks = dash?.verification;
  const [every] = useEveryCandidate();
  const views = useMemo<CandidateBar[]>(
    () =>
      candidateBars({
        index,
        viewedNode,
        line,
        sampleSet,
        marks: marks ?? [],
        every,
      }),
    [index, viewedNode, line, sampleSet, marks, every],
  );

  return {
    history,
    overlap,
    coverage,
    sampleUniverse,
    viewedNode,
    areCycles,
    views,
  };
}
