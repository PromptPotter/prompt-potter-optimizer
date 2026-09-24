"use client";
import { useCallback, useMemo, useState, type CSSProperties } from "react";
import { activeSeries, metricInkToken, type SeriesCtx } from "./series";
import { FitnessChart, type PlotGeometry, geomEqual } from "./FitnessChart";
import { DendrogramStrip } from "./DendrogramStrip";
import { AbilityHelp, ThetaCaveatNotice } from "./AbilityInfo";
import { setCandidatesState, toggleMetric, useCandidatesState } from "./candidates-store";
import {
  Badge,
  CardFrame,
  Chip,
  ChipGroup,
  CopyButton,
  HoverCard,
  IconMore,
  IconTree,
  Menu,
  MenuCheck,
  MenuRadioGroup,
  MenuSep,
  Toolbar,
  ToolbarSep,
  ToolbarSpacer,
} from "@/components/ui";
import { liveCandidates } from "@/lib/poll";
import { ABORT_LENS_LABELS } from "@/lib/api/types.generated";
import type { DashboardCandidate, RoundSummary } from "@/lib/api/types";
import { subjectKey, withMask } from "@/lib/api/reads";
import { useCompareSelection } from "@/lib/compare-selection";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { ScoringMaskEditor } from "@/components/shell/mask/ScoringMaskEditor";
import { ApplyScenarioPanel } from "@/components/dashboard/control/ApplyScenarioPanel";
import {
  criterionOf,
  lensOf,
  setScoringMask,
  subsetExactFor,
  useScoringMask,
} from "@/components/shell/mask/scoring-mask";
import { FitnessRankSummary } from "./FitnessRankSummary";
import { fetchDiagnosticRuns, type DiagnosticRunRecord } from "@/lib/api";
import type { LineageNode } from "@/lib/api";
import { readyData, useRead } from "@/lib/hooks/useRead";
import {
  barsAreCourses,
  candidateViews,
  forkKeysOf,
  HEADLINE_METRICS,
  headlineMetricLabel,
  nodeKeyOf,
  pathOf,
  sortedRounds,
  type HeadlineMetric,
} from "@/lib/derivations";
import { isSelectedCandidate } from "@/lib/types";
import { encodeCyclePath } from "@/lib/ids";
import { useWorkspace } from "@/lib/workspace";
import { useLineage } from "./useLineage";
import { useCycleEvaluators } from "@/components/shell/mask/useCycleEvaluators";
import { SampleSetControl } from "./SampleSetControl";
import { measuredUniverse } from "@/lib/sample-set";
import { useViewedLineage, divergenceRoundsFor } from "@/lib/lineage";
import { cx } from "@/lib/cx";
import { TERMS } from "@/lib/terms";
import type { CandidateView } from "@/lib/types";

// The candidates card: this cycle's population as bars, with the dendrogram on the same x spine.

// The abort rows are derived from the served `ABORT_LENS_LABELS`, never hand-listed.
const LENS_OPTIONS: readonly { value?: string; label?: string; heading?: string }[] = [
  { value: "", label: "Realized" },
  { heading: "Scoring" },
  { value: "score:accuracy", label: "Accuracy" },
  { heading: "Abort off" },
  ...Object.entries(ABORT_LENS_LABELS).map(([variant, label]) => ({
    value: `abort:${variant}`,
    label,
  })),
];

export function CandidatesCard() {
  const { dash, isLive } = useDashboard();
  const unit = dash?.measured_unit ?? "sample";
  const {
    campaignId,
    cycleId,
    leafCampaignId,
    leafCycleId,
    viewedPath,
    viewedCandidateId,
    selectCyclePath,
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
  const electedMetric: HeadlineMetric = dash?.headline_metric ?? "accuracy";

  const inflightCandidates: DashboardCandidate[] = useMemo(() => liveCandidates(dash), [dash]);

  // Keyed on `dash?.rounds`, the only slice `sortedRounds` reads.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const history: RoundSummary[] = useMemo(() => sortedRounds(dash), [dash?.rounds]);

  // Never polled: re-run `promptpotter verify` and reload for a fresh red bar.
  const diagRunsResp = readyData(
    useRead(
      {
        key: `${campaignId}\x1f${cycleId}`,
        fetch: (s) => fetchDiagnosticRuns(undefined, s),
      },
      { surface: "diagnostic-runs", auth: true },
    ),
  );
  const diagByLabel = useMemo(() => {
    const m = new Map<string, DiagnosticRunRecord>();
    if (!campaignId || !cycleId) return m;
    for (const r of diagRunsResp?.runs ?? []) {
      if (r.source_campaign !== campaignId || r.source_cycle !== cycleId) continue;
      const prior = m.get(r.source_label);
      if (!prior || r.ts > prior.ts) m.set(r.source_label, r);
    }
    return m;
  }, [diagRunsResp, campaignId, cycleId]);

  const { open: maskOpen, mask } = useScoringMask();
  const evaluators = useCycleEvaluators();
  const activeLens = maskOpen ? lensOf(mask) : null;

  // Carries the on-screen mask, so a scenario built here opens in Compare reading the same thing.
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

  // Gated on `dash`, or the seed runs before `headline_metric` arrives.
  if (cycleId && dash && metricsSeededForCycle !== cycleId) {
    setCandidatesState({
      metrics: new Set<HeadlineMetric>(["accuracy", electedMetric]),
      metricsSeededForCycle: cycleId,
    });
  }

  // The newest round's reading names the basis; the round in flight carries one from its election,
  // a whole `l1_critique` call before `rounds[]` does.
  const overlap = useMemo(
    () =>
      dash?.current_round.overlap ??
      history.reduce<RoundSummary["overlap"]>((best, r) => r.overlap ?? best, null),
    [dash?.current_round.overlap, history],
  );
  const overlapByCandidate = useMemo(
    () => new Map((overlap?.members ?? []).map((m) => [m.candidate_id, m])),
    [overlap],
  );

  const sampleUniverse = useMemo(() => measuredUniverse(history), [history]);

  const overlay = useViewedLineage();
  const { lens, setLens, maskActive, maskLabel, scoringMaskActive } = overlay;

  // The bars are the children of the VIEWED node (navigation), never of `selectedCandidate`
  // (inspection): one slot for both makes the chart its own input.
  const viewedNode = useMemo(() => {
    if (!viewedPath) return undefined;
    const entry = overlay.index.get(encodeCyclePath(viewedPath));
    if (!entry) return undefined;
    return viewedCandidateId
      ? entry.candidates.find((c) => c.id === viewedCandidateId)
      : (entry.course ?? undefined);
  }, [overlay.index, viewedPath, viewedCandidateId]);

  // The ledger snapshots a score only at completion, so a mid-scoring bar lives in
  // `dash.current_round`. Keyed by label: a live candidate has no lineage id yet.
  const inflightByLabel = useMemo(
    () => new Map(inflightCandidates.map((c) => [c.label, c])),
    [inflightCandidates],
  );

  const areCourses = useMemo(() => barsAreCourses(viewedNode), [viewedNode]);

  const views = useMemo<CandidateView[]>(
    () =>
      candidateViews({
        viewedNode,
        inflightByLabel,
        sampleSet,
        lensSubsetExact: subsetExactFor(mask),
        diagByLabel,
        overlapByCandidate,
        overlapSize: overlap?.sample_ids.length ?? null,
      }),
    [viewedNode, inflightByLabel, sampleSet, mask, diagByLabel, overlapByCandidate, overlap],
  );

  const floorPinned = useMemo(
    () => views.filter((v) => v.thetaCaveat === "floor_pinned").map((v) => v.label),
    [views],
  );

  const forkKeys = useMemo(() => forkKeysOf(viewedNode), [viewedNode]);

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
    (v: CandidateView | null) => {
      if (!v || !leafCycleId) {
        setSelectionForCandidate(null);
        return;
      }
      // A bar click INSPECTS, never navigates — a course bar included.
      setSelectionForCandidate({
        cycle_id: leafCycleId,
        round: v.round,
        candidate_id: v.candidate_id,
        label: v.label,
        accuracy: v.accuracy,
        is_winner: v.is_winner,
      });
    },
    [setSelectionForCandidate, leafCycleId],
  );

  // Navigation rides the node's own path, never a bare cycle id.
  const onFreeHierarchy = useCallback(
    (course: LineageNode) => {
      revealLane(nodeKeyOf(course));
      selectCyclePath(pathOf(course), null);
    },
    [revealLane, selectCyclePath],
  );

  const selectedKey = useMemo(
    () =>
      views.find((v) =>
        isSelectedCandidate(selectedCandidate, leafCycleId, v.round, v.candidate_id),
      )?.key ?? null,
    [views, selectedCandidate, leafCycleId],
  );

  // Served (`divergence` on the tree overlay), never derived; the apply panel mints its fork here.
  const divergentRound = useMemo(() => {
    if (!overlay.maskActive || viewedCandidateId) return null;
    const { points, subtree } = divergenceRoundsFor(overlay.index, viewedPath);
    let first = Infinity;
    for (const r of points) first = Math.min(first, r);
    for (const r of subtree) first = Math.min(first, r);
    return Number.isFinite(first) ? first : null;
  }, [overlay.maskActive, overlay.index, viewedPath, viewedCandidateId]);

  const divergenceBoundary = useMemo(() => {
    if (divergentRound == null) return null;
    const idx = views.findIndex((v) => v.round >= divergentRound);
    return idx >= 0 ? idx : null;
  }, [divergentRound, views]);

  // `dash.candidate` goes stale between rounds, so gate on the scorer being the active node.
  const inFlightIndex = useMemo(() => {
    if (!isLive) return null;
    if (dash?.current_round.active_node !== "l1_score") return null;
    const lbl = String(dash?.candidate || "").split("/")[0];
    if (!lbl) return null;
    const idx = views.findIndex((v) => v.label === lbl);
    return idx >= 0 ? idx : null;
  }, [isLive, dash?.current_round.active_node, dash?.candidate, views]);

  const lensActive = lens !== "" && !scoringMaskActive;

  // Off the served reading, not the views: those null `overlapAccuracy` for everyone off the set.
  const hasOverlap = overlap != null && !areCourses;

  if (cycleId && overlap != null && overlapSeededForCycle !== cycleId) {
    setCandidatesState({ showOverlap: true, overlapSeededForCycle: cycleId });
  }

  const pickedSet = sampleSet != null && !areCourses;
  const rung = pickedSet ? 2 : showOverlap && hasOverlap ? 1 : 0;
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
  const overlapNext = [
    hasOverlap
      ? "Read C0 and every winner since on the one set of cells all of them answered. The bars beside it stay on each candidate's own cells."
      : "Pick a set of cells and read every candidate that answered all of it on that one basis. There is no reading to show yet: the adopted line is still C0 alone, and a second member arrives with the first round that promotes a winner — a held round leaves nothing to read C0 against.",
    "Choose which cells the overlap bars are read on — any round's set, or your own pick.",
    "Hide the overlap bars and drop the picked set.",
  ];

  const seriesCtx = useMemo<SeriesCtx>(
    () => ({
      metrics,
      showMask: maskOpen,
      showCache,
      showOverlap: rung > 0,
      views,
      unit,
      electedMetric,
    }),
    [metrics, maskOpen, showCache, rung, views, unit, electedMetric],
  );
  const legend = useMemo(
    () => activeSeries(seriesCtx).filter((s) => s.metric == null),
    [seriesCtx],
  );

  const cacheHitCount = useMemo(
    () => views.filter((v) => (v.cached_samples ?? 0) > 0).length,
    [views],
  );

  const forestToggle = (
    <Chip
      icon={totalDescendants === 0}
      on={showForest}
      ariaLabel={
        showForest
          ? "Hide the lineage forest"
          : `Show the lineage forest — the full campaign tree, ${totalDescendants} descendant${totalDescendants === 1 ? "" : "s"}`
      }
      title={`${showForest ? "Hide" : "Show"} the campaign tree — every cycle and fork side by side (${totalDescendants} descendant${totalDescendants === 1 ? "" : "s"})`}
      onClick={() => setShowForest(!showForest)}
    >
      <span className="cand-forest-toggle">
        <IconTree />
        {totalDescendants > 0 && <span className="cand-view-count">{totalDescendants}</span>}
      </span>
    </Chip>
  );

  return (
    <CardFrame
      className={cx(
        "cand-card",
        maskOpen && "mask-open",
      )}
      title={
        <Toolbar className="cand-toolbar">
          {viewedCandidateId && viewedPath ? (
            <button
              type="button"
              className="cand-title cand-crumb"
              onClick={() => selectCyclePath(viewedPath, null)}
              title="Back to this course's candidates"
            >
              ‹ {viewedNode?.label ?? "runs"} · runs
            </button>
          ) : (
            <span className="cand-title">Candidates</span>
          )}
          {maskActive && (
            <Badge
              tone="danger"
              title={`Showing the ${maskLabel} mask — divergence vs the realized record`}
            >
              {maskLabel}
            </Badge>
          )}
          <ToolbarSep />
          {/* Display only — the engine gates on θ whatever is lit here. */}
          <ChipGroup label="Bars" joined>
            {HEADLINE_METRICS.map((m) => (
              <Chip
                key={m.id}
                icon
                on={metrics.has(m.id)}
                ink={`var(${metricInkToken(m.id, electedMetric)})`}
                ariaLabel={headlineMetricLabel(m.id)}
                title={m.title}
                onClick={() => toggleMetric(m.id)}
              >
                {m.glyph}
              </Chip>
            ))}
            <Chip
              icon
              on={rung > 0}
              ink={rung === 2 ? "var(--color-new)" : "var(--color-overlap)"}
              disabled={overlapDisabled}
              ariaLabel={
                rung === 2
                  ? "Choosing which cells the overlap bars are read on; press to turn them off"
                  : rung === 1
                    ? "Overlap shown — press to choose its cells"
                    : hasOverlap
                      ? "Show the overlap reading — the adopted line on one shared set of cells"
                      : "Pick a set of cells to read the candidates on"
              }
              title={
                areCourses
                  ? "These bars are runs, not scored cells — open a run to compare its candidates."
                  : overlapNext[rung]
              }
              onClick={stepOverlap}
            >
              ∩
            </Chip>
          </ChipGroup>
          <ToolbarSpacer />
          <Menu
            renderTrigger={({ open, toggle }) => (
              <Chip
                icon
                on={open || lensActive || maskOpen || showCache}
                ariaLabel="More candidate options"
                title="Lens, scoring mask, cache overlay, and the θ explainer"
                onClick={toggle}
              >
                <IconMore />
              </Chip>
            )}
          >
            {({ close }) => (
              <>
                <MenuRadioGroup
                  label={scoringMaskActive ? "Lens — driven by the scoring mask" : "Lens"}
                  value={scoringMaskActive ? "" : lens}
                  options={LENS_OPTIONS}
                  onChange={(v) => {
                    if (scoringMaskActive) return;
                    setLens(v);
                    close();
                  }}
                />
                <MenuSep />
                <HoverCard content="Pick evaluators and reweight them to recompute every score under a criterion you choose.">
                  <MenuCheck on={maskOpen} onClick={() => setScoringMask({ open: !maskOpen })}>
                    Scoring mask
                  </MenuCheck>
                </HoverCard>
                {/* Never disabled: the origin is normally the replayed one. */}
                <HoverCard content={TERMS.cache_replayed}>
                  <MenuCheck
                    on={showCache}
                    onClick={() => setCandidatesState({ showCache: !showCache })}
                  >
                    {/* "Replayed", never "cache": `cache` names the provider's prefix discount elsewhere. */}
                    Replayed{cacheHitCount > 0 ? ` · ${cacheHitCount} of ${views.length}` : ""}
                  </MenuCheck>
                </HoverCard>
                <MenuSep />
                <MenuCheck
                  on={!!compareKey && comparing.hasSubject(compareKey)}
                  disabled={!compareKey || !campaignId}
                  onClick={() => {
                    if (!compareKey || !campaignId) return;
                    if (comparing.hasSubject(compareKey)) comparing.remove(compareKey);
                    else comparing.addSubject({ rootCampaignId: campaignId, subject: compareKey });
                    close();
                  }}
                  title={
                    compareKey
                      ? "Read this searchpoint beside other campaigns, branches and searchpoints on the Compare tab."
                      : "Pick a candidate first — a bar, a dendrogram node or a forest stub."
                  }
                >
                  Compare this searchpoint
                </MenuCheck>
                <MenuSep />
                <MenuCheck
                  on={showTheta}
                  onClick={() => setShowTheta((v) => !v)}
                  title="Why a lower-accuracy candidate can win"
                >
                  How candidates are ranked
                </MenuCheck>
                {showTheta && (
                  <AbilityHelp
                    model={history.at(-1)?.ability?.calibration_model ?? null}
                    caveat={history.at(-1)?.ability?.caveat ?? null}
                  />
                )}
              </>
            )}
          </Menu>
          <CopyButton data={views} title="Copy all candidates as JSON" />
        </Toolbar>
      }
    >
      <div className="fitness-body">
        {!areCourses && (
          <ThetaCaveatNotice
            caveat={history.at(-1)?.ability?.caveat ?? null}
            ability={history.at(-1)?.ability ?? null}
          />
        )}
        {!areCourses && floorPinned.length > 0 && (
          <>
            <ThetaCaveatNotice caveat="floor_pinned" />
            <div className="theta-caveat-arms">Affected: {floorPinned.join(", ")}.</div>
          </>
        )}
        {sampleSet && !areCourses && (
          <SampleSetControl rounds={history} overlap={overlap} unit={unit} />
        )}
        {/* The dendrogram's x-alignment depends on sharing this box with the canvas. */}
        <div className="fitness-chart-wrap">
          {legend.length > 0 && (
            <div className="fitness-legend">
              {legend.map((s) => (
                <span key={s.key} title={s.hint?.(seriesCtx)}>
                  <span
                    className={cx("swatch", s.kind === "line" && "line", s.hollow && "hollow")}
                    style={{ "--ink": `var(${s.ink(seriesCtx)})` } as CSSProperties}
                  />
                  {s.legend?.(seriesCtx)}
                </span>
              ))}
            </div>
          )}
          <FitnessChart
            views={views}
            metrics={metrics}
            showMask={maskOpen}
            showOverlap={rung > 0}
            showCache={showCache}
            divergenceBoundary={divergenceBoundary}
            inFlightIndex={inFlightIndex}
            selectedKey={selectedKey}
            onSelect={onSelect}
            onGeometry={onGeometry}
            unit={unit}
            electedMetric={electedMetric}
          />
          <div className="cand-tree-row">
            {!viewedCandidateId && (
              <DendrogramStrip
                views={views}
                plot={plot}
                metric={metric}
                selectedKey={selectedKey}
                onSelect={onSelect}
                forkedFrom={forkedFrom}
                forkKeys={forkKeys}
                onFreeHierarchy={onFreeHierarchy}
              />
            )}
            {forestToggle}
          </div>
        </div>
        {maskOpen && !viewedCandidateId && (
          <ScoringMaskEditor
            rows={evaluators.rows}
            inActive={evaluators.inActive}
            mask={mask}
            onMask={(next) => setScoringMask({ mask: next })}
            seeded={evaluators.seeded}
            // No samples field: the chip strip owns that axis.
            summary={<FitnessRankSummary views={views} criterion={activeLens != null} />}
          />
        )}
        {maskOpen && !viewedCandidateId && (
          <ApplyScenarioPanel
            campaignId={campaignId}
            cycleId={cycleId}
            isLive={isLive}
            criterion={criterionOf(mask)}
            divergentRound={divergentRound}
            nextRound={history.length}
          />
        )}
      </div>
    </CardFrame>
  );
}
