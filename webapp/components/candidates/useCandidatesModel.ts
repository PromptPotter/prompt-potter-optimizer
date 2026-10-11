"use client";
import { useCallback, useMemo, useState } from "react";
import { activeSeries, type SeriesCtx } from "./series";
import { type PlotGeometry, geomEqual } from "./FitnessChart";
import { setCandidatesState, useCandidatesState } from "@/lib/candidates-store";
import { useCandidateBars } from "./useCandidateBars";
import { measuringLabel, useCycleStream, type DashboardSnapshot } from "@/lib/poll";
import { subjectKey, withMask } from "@/lib/api/reads";
import { useCompareSelection } from "@/lib/compare-selection";
import { useSelection } from "@/lib/SelectionContext";
import { lensOf, useScoringMask } from "@/lib/scoring-mask";
import type { CourseNode } from "@/lib/api";
import { nodeKeyOf, pathOf, type DisplayMetric } from "@/lib/derivations";
import { isSelectedCandidate, selectedCandidateOf } from "@/lib/types";
import { encodeCyclePath } from "@/lib/ids";
import { useWorkspace } from "@/lib/workspace";
import { useLineage } from "@/lib/hooks/useLineage";
import { useServedCriterion } from "@/lib/hooks/useServedCriterion";
import { useViewedLineage, divergenceRoundsFor } from "@/lib/lineage";
import type { CandidateBar } from "@/lib/types";
import { roundOf } from "./dendrogram";

export type CandidatesModel = ReturnType<typeof useCandidatesModel>;

export function useCandidatesModel(dash: DashboardSnapshot) {
  const { isLive } = useCycleStream();
  const unit = dash.measured_unit;
  const {
    campaignId,
    cycleId,
    leafCampaignId,
    leafCycleId,
    viewedPath,
    viewedCandidateId,
    navigate,
  } = useWorkspace();
  const {
    candidate: selectedCandidate,
    setSelectionForCandidate,
    sampleSet,
    setSelectionForSampleSet,
  } = useSelection();
  const comparing = useCompareSelection();

  const {
    showForest,
    metrics,
    metricsSeededForCycle,
    showOverlap,
    overlapSeededForCycle,
    showCache,
  } = useCandidatesState();
  const electedMetric = dash.display_metric;
  const wonOnTheta = dash.elects_on === "ability";

  const overlay = useViewedLineage();
  const { lens, setLens, maskActive, maskLabel, scoringMaskActive } = overlay;
  const { history, overlap, sampleUniverse, viewedNode, areCourses, views, floorPinned } =
    useCandidateBars(overlay.index);

  const { open: maskOpen, mask } = useScoringMask();
  const served = useServedCriterion();
  const activeLens = maskOpen ? lensOf(mask) : null;

  const compareKey =
    selectedCandidate && leafCampaignId && viewedPath
      ? withMask(
          subjectKey(
            "candidate",
            [leafCampaignId, selectedCandidate.cycle_id, selectedCandidate.candidate_id],
            viewedPath.slice(0, -1),
          ),
          { lens: activeLens, samples: sampleSet?.length ? sampleSet.join(",") : null },
        )
      : null;

  if (cycleId && metricsSeededForCycle !== cycleId) {
    setCandidatesState({
      metrics: new Set<DisplayMetric>(["accuracy", electedMetric]),
      metricsSeededForCycle: cycleId,
    });
  }

  const { metric, forkedFrom, revealLane, setShowForest, totalDescendants } = useLineage({
    campaignId,
    cycleId,
    path: viewedPath,
    electedMetric,
  });

  const [showTheta, setShowTheta] = useState(false);
  const [plot, setPlot] = useState<PlotGeometry | null>(null);
  const onGeometry = useCallback((g: PlotGeometry) => {
    setPlot((prev) => (geomEqual(prev, g) ? prev : g));
  }, []);

  const onSelect = useCallback(
    (v: CandidateBar | null) => {
      if (!v || !leafCycleId) {
        setSelectionForCandidate(null);
        return;
      }
      setSelectionForCandidate(selectedCandidateOf(leafCycleId, roundOf(v), v.node.id, v.label));
    },
    [setSelectionForCandidate, leafCycleId],
  );

  const onFreeHierarchy = useCallback(
    (course: CourseNode) => {
      revealLane(nodeKeyOf(course));
      navigate(pathOf(course));
    },
    [revealLane, navigate],
  );

  const selectedKey = useMemo(
    () =>
      views.find((v) =>
        isSelectedCandidate(selectedCandidate, leafCycleId, roundOf(v), v.node.id),
      )?.key ?? null,
    [views, selectedCandidate, leafCycleId],
  );

  const divergentRound = useMemo(() => {
    if (!overlay.maskActive || viewedCandidateId) return null;
    const { points, subtree } = divergenceRoundsFor(overlay.index, viewedPath);
    let first = Infinity;
    for (const r of points) first = Math.min(first, r);
    for (const r of subtree) first = Math.min(first, r);
    return Number.isFinite(first) ? first : null;
  }, [overlay.maskActive, overlay.index, viewedPath, viewedCandidateId]);

  const lensCriterion =
    (viewedPath && overlay.index.get(encodeCyclePath(viewedPath))?.course?.lens_criterion) || null;

  const divergenceBoundary = useMemo(() => {
    if (divergentRound == null) return null;
    const idx = views.findIndex((v) => roundOf(v) >= divergentRound);
    return idx >= 0 ? idx : null;
  }, [divergentRound, views]);

  const measuring = measuringLabel(dash);
  const measuringRound = dash.current_round.round;
  const inFlightIndex = useMemo(() => {
    if (!isLive || measuring === null) return null;
    // `findLastIndex`: a fork's timeline also holds the parent's arm of that label, earlier on it.
    const idx = views.findLastIndex(
      (v) =>
        v.arm?.reading.arm.label === measuring && v.arm.reading.arm.round === measuringRound,
    );
    return idx >= 0 ? idx : null;
  }, [isLive, measuring, measuringRound, views]);

  const lensActive = lens !== "" && !scoringMaskActive;

  // Off the served reading, not the views, which null `overlap` for everyone off the set.
  const hasOverlap = overlap != null && !areCourses;

  if (cycleId && overlap != null && overlapSeededForCycle !== cycleId) {
    setCandidatesState({ showOverlap: true, overlapSeededForCycle: cycleId });
  }

  const pickedSet = sampleSet != null && !areCourses;
  const rung = pickedSet ? 2 : showOverlap && hasOverlap ? 1 : 0;
  const overlapBasis = sampleSet != null ? sampleSet.length : dash.overlap_line_n;
  const overlapDisabled = areCourses || (!hasOverlap && sampleUniverse.length === 0);
  const stepOverlap = () => {
    if (rung === 2) {
      setSelectionForSampleSet(null);
      setCandidatesState({ showOverlap: false });
    } else if (rung === 1 || !hasOverlap) {
      setSelectionForSampleSet(overlap?.sample_ids ?? sampleUniverse);
      setCandidatesState({ showOverlap: true });
    } else {
      setCandidatesState({ showOverlap: true });
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
      electedMetric,
    }),
    [metrics, maskOpen, showCache, rung, views, overlapBasis, unit, electedMetric],
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
    campaignId,
    cycleId,
    isLive,
    unit,
    viewedPath,
    viewedCandidateId,
    viewedLabel: viewedNode?.label ?? "runs",
    navigate,
    history,
    ability: history.at(-1)?.ability ?? null,
    electedMetric,
    wonOnTheta,
    views,
    areCourses,
    floorPinned,
    metrics,
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
    lensShift: viewedNode?.kind === "course" ? viewedNode.lens_shift : null,
    maskActive,
    maskLabel,
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
