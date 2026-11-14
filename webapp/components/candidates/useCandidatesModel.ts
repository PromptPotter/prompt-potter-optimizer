import { useCallback, useMemo, useState } from "react";
import { activeSeries, type SeriesCtx } from "./series";
import { type PlotGeometry, geomEqual } from "./FitnessChart";
import {
  setCandidatesState,
  showOverlapOf,
  toggleMetric,
  useCandidatesState,
  useMetrics,
} from "@/lib/state/candidates";
import { useCandidateBars } from "./useCandidateBars";
import { useViewedDashboard } from "@/lib/state/poll";
import { candidateSubject, withMask } from "@/lib/api/reads";
import { useCompareSelection } from "@/lib/state/compare-selection";
import { useSelection } from "@/lib/state/selection";
import { lensOf, useMaskState } from "@/lib/state/scoring-mask";
import { useCellTerms } from "@/lib/hooks/useConnector";
import type { HierarchyCourse, ServedDashboard, DisplayMetric } from "@/lib/api/types";
import {
  candidatesAtPath,
  courseAt,
  type LineageIndex,
} from "@/lib/derivations";
import { pathOfAddress, type CyclePath } from "@/lib/ids";
import { useWorkspace } from "@/lib/state/workspace";
import { useCampaignForest } from "@/lib/hooks/useCampaignForest";
import { useServedCriterion } from "@/lib/hooks/useServedCriterion";
import { useViewedCourse, useViewedLineage } from "@/lib/state/lineage";
import type { CandidateBar } from "@/lib/types";
import { barRound } from "./dendrogram";

export type CandidatesModel = ReturnType<typeof useCandidatesModel>;

export function useCandidatesModel(dash: ServedDashboard) {
  const { isLive } = useViewedDashboard();
  const unit = dash.measured_unit;
  const { viewed, pick, pickCandidate, navigate, setViewKeys } = useWorkspace();
  const campaignId = viewed?.root.campaignId ?? null;
  const cycleId = viewed?.root.cycleId ?? null;
  const viewedPath = viewed?.path ?? null;
  const { sampleSet } = useSelection();
  const comparing = useCompareSelection();

  const { showOverlap: overlapPicked, showCache } = useCandidatesState();
  const displayMetric = dash.display_metric;
  const metrics = useMetrics(cycleId, displayMetric);
  const overlay = useViewedLineage();
  const wonOnTheta = useViewedCourse()?.facts.elects_on === "ability";
  const { lens, setLens, maskActive, readUnder, scoringMaskActive } = overlay;
  const { history, overlap, coverage, sampleUniverse, viewedNode, areCourses, views, floorPinned } =
    useCandidateBars(overlay.index);

  const { open: maskOpen, mask } = useMaskState();
  const served = useServedCriterion();
  const cellTerms = useCellTerms();
  const activeLens = maskOpen ? lensOf(mask, cellTerms) : null;

  const compareKey =
    pick && viewed
      ? withMask(candidateSubject(viewed.path, pick.arm), {
          lens: activeLens, samples: sampleSet?.length ? sampleSet.join(",") : null,
        })
      : null;

  const { metric, forkedFrom, revealLane, showForest, setShowForest, totalDescendants } =
    useCampaignForest({
    campaignId,
    cycleId,
    path: viewedPath,
    displayMetric,
  });

  const [showTheta, setShowTheta] = useState(false);
  const [plot, setPlot] = useState<PlotGeometry | null>(null);
  const onGeometry = useCallback((g: PlotGeometry) => {
    setPlot((prev) => (geomEqual(prev, g) ? prev : g));
  }, []);

  const onSelect = useCallback(
    (v: CandidateBar | null) => {
      // A course bar is no arm: there is nothing to inspect.
      pickCandidate(v?.reading ? { round: barRound(v), arm: v.reading.arm.key } : null);
    },
    [pickCandidate],
  );

  const onFreeHierarchy = useCallback(
    (course: HierarchyCourse) => {
      revealLane(course.address);
      navigate({ path: pathOfAddress(course.address) });
    },
    [revealLane, navigate],
  );

  const pickedAddress = pick?.address ?? null;
  const selectedKey = useMemo(
    () => (views.some((v) => v.key === pickedAddress) ? pickedAddress : null),
    [views, pickedAddress],
  );
  const drilled = viewedNode?.level === "candidate";

  const divergentRound = useMemo(() => {
    if (!overlay.maskActive || drilled) return null;
    const { points, subtree } = divergenceRoundsFor(overlay.index, viewedPath);
    let first = Infinity;
    for (const r of points) first = Math.min(first, r);
    for (const r of subtree) first = Math.min(first, r);
    return Number.isFinite(first) ? first : null;
  }, [overlay.maskActive, overlay.index, viewedPath, drilled]);

  const lensCriterion =
    courseAt(overlay.index, viewedPath)?.facts.lens_criterion ?? null;

  const divergenceBoundary = useMemo(() => {
    if (divergentRound == null) return null;
    const idx = views.findIndex((v) => barRound(v) >= divergentRound);
    return idx >= 0 ? idx : null;
  }, [divergentRound, views]);

  const measuring = dash.measuring_arm;
  const inFlightIndex = useMemo(() => {
    if (!isLive || measuring === null) return null;
    // `findLastIndex`: a fork's timeline also holds the parent's arm of that key, earlier on it.
    const idx = views.findLastIndex(
      (v) => v.reading?.arm.key === measuring.key && v.reading.arm.round === measuring.round,
    );
    return idx >= 0 ? idx : null;
  }, [isLive, measuring, views]);

  const lensActive = lens !== "" && !scoringMaskActive;

  // Off the served reading, not the views, which null `overlap` for everyone off the set.
  const hasOverlap = overlap != null && !areCourses;

  const showOverlap = showOverlapOf(overlapPicked, cycleId, overlap != null);
  const setShowOverlap = (on: boolean) =>
    cycleId !== null && setCandidatesState({ showOverlap: { cycleId, value: on } });

  const pickedSet = sampleSet != null && !areCourses;
  const rung = pickedSet ? 2 : showOverlap && hasOverlap ? 1 : 0;
  const overlapBasis = sampleSet != null ? sampleSet.length : dash.overlap_line_n;
  const overlapDisabled = areCourses || (!hasOverlap && sampleUniverse.length === 0);
  const stepOverlap = () => {
    if (rung === 2) {
      setViewKeys({ samples: null });
      setShowOverlap(false);
    } else if (rung === 1 || !hasOverlap) {
      setViewKeys({ samples: overlap?.positions ?? sampleUniverse });
      setShowOverlap(true);
    } else {
      setShowOverlap(true);
    }
  };

  const seriesCtx = useMemo<SeriesCtx>(
    () => ({
      metrics,
      showMask: maskOpen,
      showCache,
      showOverlap: rung > 0,
      views,
      overlapBasis,
      unit,
      displayMetric,
    }),
    [metrics, maskOpen, showCache, rung, views, overlapBasis, unit, displayMetric],
  );
  const legend = useMemo(
    () => activeSeries(seriesCtx).filter((s) => s.metric == null),
    [seriesCtx],
  );

  const cacheHitCount = useMemo(
    () => views.filter((v) => (v.reading?.panel.cached ?? 0) > 0).length,
    [views],
  );

  const compareOn = !!compareKey && comparing.hasSubject(compareKey);
  const toggleCompare = () => {
    if (!compareKey || !campaignId) return;
    if (comparing.hasSubject(compareKey)) comparing.remove(compareKey);
    else comparing.addSubject({ rootCampaignId: campaignId, subject: compareKey });
  };

  return {
    isLive,
    unit,
    viewedPath,
    drilled,
    viewedLabel: viewedNode?.label ?? "runs",
    navigate,
    pickCandidate,
    history,
    coverage,
    ability: history.at(-1)?.series_reading ?? null,
    displayMetric,
    wonOnTheta,
    views,
    areCourses,
    floorPinned,
    metrics,
    toggleMetric: (m: DisplayMetric) => toggleMetric(m, cycleId, metrics),
    showCache,
    cacheHitCount,
    showForest,
    setShowForest,
    totalDescendants,
    metric,
    forkedFrom,
    showTheta,
    toggleTheta: () => setShowTheta((v) => !v),
    plot,
    onGeometry,
    onSelect,
    onFreeHierarchy,
    selectedKey,
    maskOpen,
    mask,
    anchors: served.anchors,
    criterionActive: activeLens != null,
    lens,
    setLens,
    lensActive,
    lensCriterion,
    lensShift: viewedNode?.level === "course" ? (viewedNode.facts.lens_shift ?? null) : null,
    maskActive,
    readUnder,
    scoringMaskActive,
    divergentRound,
    divergenceBoundary,
    inFlightIndex,
    overlap,
    hasOverlap,
    pickedSet,
    rung,
    overlapDisabled,
    stepOverlap,
    seriesCtx,
    legend,
    hasCompareKey: compareKey != null,
    compareOn,
    compareDisabled: !compareKey || !campaignId,
    toggleCompare,
  };
}

// served: `divergence` rides the round's WINNER; `divergent` every candidate of a counterfactual round.
function divergenceRoundsFor(
  index: LineageIndex,
  path: CyclePath | null,
): { points: ReadonlySet<number>; subtree: ReadonlySet<number> } {
  const points = new Set<number>();
  const subtree = new Set<number>();
  // Addressed, not scanned: inner cycle ids repeat across sandboxes.
  for (const cand of candidatesAtPath(index, path)) {
    const { round } = cand.facts.reading.arm;
    if (cand.facts.divergence) points.add(round);
    if (cand.facts.divergent) subtree.add(round);
  }
  return { points, subtree };
}
