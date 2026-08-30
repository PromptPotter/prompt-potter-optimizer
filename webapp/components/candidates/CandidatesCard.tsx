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
  Menu,
  MenuCheck,
  MenuRadioGroup,
  MenuSep,
  Toolbar,
  ToolbarSep,
  ToolbarSpacer,
} from "@/components/ui";
import { IconMore, IconTree } from "./toolbar-icons";
import { liveCandidates } from "@/lib/poll";
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
import { useAuth } from "@/lib/auth-context";
import { useFetch } from "@/lib/hooks/useFetch";
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
import { useCycleEvaluators } from "./useCycleEvaluators";
import { SampleSetControl } from "./SampleSetControl";
import { measuredUniverse } from "@/lib/sample-set";
import { useViewedLineage, divergenceRoundsFor } from "@/lib/lineage";
import { cx } from "@/lib/cx";
import type { CandidateView } from "@/lib/types";

// The candidates card — this cycle's population and its ancestry, in one surface.
//
// The bars and the dendrogram under them ride the SAME flat candidate spine, and the bar
// chart's x categories ARE that spine — everything here is bound to that alignment.
//
// The multi-cycle FOREST is deliberately NOT in here: it shares no axis with the bars, so it
// lives in `ForestCard` and the quiet toggle beside the dendrogram is its only trace here.
//
// `heading` rows are optgroup labels; the rest are pickable. One flat list, so the
// menu markup stays a map() instead of nested groups.
const LENS_OPTIONS: readonly { value?: string; label?: string; heading?: string }[] = [
  { value: "", label: "Realized" },
  { heading: "Scoring" },
  { value: "score:accuracy", label: "Accuracy" },
  { heading: "Abort off" },
  { value: "abort:epsilon_off", label: "No ε-elimination" },
  { value: "abort:lock_in_off", label: "No lock-in" },
  { value: "abort:all_off", label: "No early abort" },
];

export function CandidatesCard() {
  // Self-sourced: live snapshot from the cycle stream, (campaignId, cycleId)
  // from the workspace. `cycleId` scopes the one-shot evaluator-seed.
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
  // Shared candidate selection — driving any of {bar, dendrogram node, forest
  // stub} sets this context slot; every other surface re-renders highlighted, and
  // the round axis in the optimizer card follows to the round that produced it.
  const {
    candidate: selectedCandidate,
    setSelectionForCandidate,
    sampleSet,
    setSelectionForSampleSet,
  } = useSelection();
  // The Compare tab's subject set, shell-level so it survives the navigation between picking one
  // searchpoint and picking the next.
  const comparing = useCompareSelection();

  const {
    showForest,
    metrics,
    metricsSeededForCycle,
    showOverlap,
    overlapSeededForCycle,
    showCache,
  } = useCandidatesState();
  // The metric this campaign's ENGINE elects on (served `CampaignConfig.headline_metric`,
  // usually θ). It seeds the second bar AND decides which series reads at full accent, so
  // the loudest bar on the chart is the one the round was actually decided on.
  const electedMetric: HeadlineMetric = dash?.headline_metric ?? "accuracy";

  // ── 1. In-flight candidates from the live dashboard. Memoized on `dash` so identity is
  // stable across polls (and across no-op 304 ticks), which is what lets the evaluator hook
  // below converge instead of looping setState every render.
  const inflightCandidates: DashboardCandidate[] = useMemo(() => liveCandidates(dash), [dash]);

  // ── 2. Completed-round summaries from `dash.rounds[]` — sole source for historical bars, so
  // the chart never stitches live + round-file fetches. Keyed on `dash?.rounds` (the only slice
  // `sortedRounds` reads) so unrelated per-poll mutations don't re-sort.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const history: RoundSummary[] = useMemo(() => sortedRounds(dash), [dash?.rounds]);

  // ── 2b. Diagnostic-run records — one per `promptpotter verify`. Fetched per cycle switch,
  // never polled: re-run verify and reload for a fresh red bar. Gated on a confirmed session,
  // because the route is workspace-scoped and 401s for anon (I5).
  const { status } = useAuth();
  const { data: diagRunsResp } = useFetch(
    status === "authed" ? (s) => fetchDiagnosticRuns(undefined, s) : null,
    [status, campaignId, cycleId],
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

  // ── 3. The scoring mask: the shared value, plus this cycle's own evaluator rows. The hook is
  // called UNCONDITIONALLY — see its own warning: `lib/lineage.tsx` reads the same store to build
  // the tree's `?lens=`, so a seed deferred to the panel's mount costs an unmasked refetch and one
  // wrong frame.
  const { open: maskOpen, mask } = useScoringMask();
  const evaluators = useCycleEvaluators({ cycleId, dash, inflightCandidates, history });
  // The criterion on screen. Derived once — three consumers asking `lensOf` separately is three
  // chances to disagree about whether the panel is even open.
  const activeLens = maskOpen ? lensOf(mask) : null;

  // The Compare address of the selected searchpoint, CARRYING whatever mask is on screen: a
  // scenario built here opens over there as a channel reading the same thing, rather than being
  // retyped into a second editor.
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

  // Render-phase seed of the metric axis, once per cycle: accuracy plus the campaign's served
  // `headline_metric`. Gated on `dash`, or the first poll seeds before that field arrives and
  // ignores the campaign's own choice.
  if (cycleId && dash && metricsSeededForCycle !== cycleId) {
    setCandidatesState({
      metrics: new Set<HeadlineMetric>(["accuracy", electedMetric]),
      metricsSeededForCycle: cycleId,
    });
  }

  // The adopted line's shared reading, off the LATEST round that has one: the set drifts as the
  // line grows, so the newest round names the basis. Served, only re-keyed here by candidate id,
  // and its `sample_ids` is the picker's quick-pick — same set by construction.
  //
  // The round in flight is newer than every closed one, so it wins outright when it has a reading.
  // It gets one at its ELECTION — the pass is measured there and quarantined behind every decision
  // the round makes — which is a whole `l1_critique` call before `rounds[]` would carry it.
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

  // The measured-sample universe a basis can be built out of — used to seed the
  // set when the operator first turns the mode on. The chip strip + per-round
  // picks + sample-trajectory drill all live in `SampleSetControl`.
  const sampleUniverse = useMemo(() => measuredUniverse(history), [history]);

  // The shared served overlay — the node's own `lens_value` is the masked bar value
  // (R-36, never recomputed here), and its divergence facts drive the boundary below.
  const overlay = useViewedLineage();
  const { lens, setLens, maskActive, maskLabel, scoringMaskActive } = overlay;

  // ── ONE RULE: the bars are the CHILDREN of the VIEWED node — a course's timeline, or the
  // courses that measured a candidate. `viewedPath` + `viewedCandidateId` is NAVIGATION, written
  // only by the tree, and deliberately not `selectedCandidate` (INSPECTION, written by a bar
  // click): one slot for both makes the chart its own input.
  const viewedNode = useMemo(() => {
    if (!viewedPath) return undefined;
    const entry = overlay.index.get(encodeCyclePath(viewedPath));
    if (!entry) return undefined;
    return viewedCandidateId
      ? entry.candidates.find((c) => c.id === viewedCandidateId)
      : (entry.course ?? undefined);
  }, [overlay.index, viewedPath, viewedCandidateId]);

  // The one thing the tree cannot answer: the candidate being scored RIGHT NOW. The ledger
  // mints a candidate before measuring it but only snapshots the score at completion, so a
  // mid-scoring bar lives in `dash.current_round`. One source per data class — history =
  // tree, live = `current_round`. Keyed by label: a course's OWN candidates keep their
  // minted label, and `dash` is the viewed course's telemetry because `viewedPath` IS its
  // address.
  const inflightByLabel = useMemo(
    () => new Map(inflightCandidates.map((c) => [c.label, c])),
    [inflightCandidates],
  );

  // Bars stay on their own measured value when they are courses, and the control below says
  // why — the rule itself lives with the assembly it constrains.
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

  const forkKeys = useMemo(() => forkKeysOf(viewedNode), [viewedNode]);

  // Only what the BARS need from the lineage: the metric they paint, the fork
  // marks on the dendrogram, and the descendant count on the forest toggle. The
  // tree itself — forests, overlays, cleanup — moved out with `ForestCard`.
  const { metric, forkedFrom, revealLane, setShowForest, totalDescendants } = useLineage({
    campaignId,
    cycleId,
    path: viewedPath,
    electedMetric,
  });

  // The bar chart's plot geometry, published by its `xBridge` plugin — the one thing the
  // dendrogram needs to sit under the right bars. `geomEqual` returning `prev` bails React out
  // of the render, which is what keeps a window resize from ever reaching it.
  // Local, not on the store: the θ explainer is a one-off read inside an already
  // ephemeral menu — nothing to preserve across a tab swap.
  const [showTheta, setShowTheta] = useState(false);
  const [plot, setPlot] = useState<PlotGeometry | null>(null);
  const onGeometry = useCallback((g: PlotGeometry) => {
    setPlot((prev) => (geomEqual(prev, g) ? prev : g));
  }, []);

  // Stable: it rides the chart's `options` memo, so an inline arrow here would
  // force a chart.update() on every 2s poll tick (and defeat FitnessChart's memo).
  const onSelect = useCallback(
    (v: CandidateView | null) => {
      if (!v || !leafCycleId) {
        setSelectionForCandidate(null);
        return;
      }
      // A bar click INSPECTS, never navigates: the chart must not move under the cursor that
      // clicked it, and a course bar is no exception. Atomic candidate+round write, so the
      // optimizer card re-anchors on the round that produced this candidate.
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

  // The ⑂ click: free the hierarchy. The bars plot one cycle, so a sibling has
  // nowhere to be drawn among them — reveal the forest below (which can draw it),
  // with that cycle expanded and in view. The bars stay put. Navigation rides the
  // node's own path, never a bare cycle id.
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

  // The round where the active lens first parts ways with the realized record — served
  // (`divergence` on the tree overlay), read here and never derived. It is the SAME fact the
  // apply panel below acts on: a fork carrying this criterion is minted exactly here, because
  // rounds before it are a stretch both readings agree on. null whenever no mask is active or
  // nothing diverges. Parked on a candidate, the bars are sibling courses inside ONE round, so
  // there is no round boundary at all.
  const divergentRound = useMemo(() => {
    if (!overlay.maskActive || viewedCandidateId) return null;
    const { points, subtree } = divergenceRoundsFor(overlay.index, viewedPath);
    let first = Infinity;
    for (const r of points) first = Math.min(first, r);
    for (const r of subtree) first = Math.min(first, r);
    return Number.isFinite(first) ? first : null;
  }, [overlay.maskActive, overlay.index, viewedPath, viewedCandidateId]);

  // …and the bar it lands on, so the chart can draw its divider at that bar's left edge.
  const divergenceBoundary = useMemo(() => {
    if (divergentRound == null) return null;
    const idx = views.findIndex((v) => v.round >= divergentRound);
    return idx >= 0 ? idx : null;
  }, [divergentRound, views]);

  // The bar of the candidate currently accumulating samples — it blinks while
  // live. The scoring candidate is `dash.candidate` ("C2.3/4"); gate on the
  // active node being the scorer + a live connection so a frozen/closed cycle
  // never pulses, and a between-rounds stale `candidate` doesn't either.
  const inFlightIndex = useMemo(() => {
    if (!isLive) return null;
    if (dash?.current_round.active_node !== "l1_score") return null;
    const lbl = String(dash?.candidate || "").split("/")[0];
    if (!lbl) return null;
    const idx = views.findIndex((v) => v.label === lbl);
    return idx >= 0 ? idx : null;
  }, [isLive, dash?.current_round.active_node, dash?.candidate, views]);

  const lensActive = lens !== "" && !scoringMaskActive;

  // Off the SERVED reading, not the bars: the completeness rule nulls `overlapAccuracy` for
  // everyone off the set, so the views would say "no reading" exactly where one exists.
  const hasOverlap = overlap != null && !areCourses;

  // Seeded ON the render a reading first appears, so the default view is what it always was.
  // Latched per cycle, like the metric axis — the operator can then turn it off and it stays
  // off for that cycle.
  if (cycleId && overlap != null && overlapSeededForCycle !== cycleId) {
    setCandidatesState({ showOverlap: true, overlapSeededForCycle: cycleId });
  }

  // ONE control, three rungs: SHOW the overlap bars on the served set, then CHOOSE which cells
  // that set is. Neither touches the metric bars. The rung is DERIVED from
  // `SelectionContext.sampleSet`, which already owns which cells, rather than a second copy.
  const pickedSet = sampleSet != null && !areCourses;
  const rung = pickedSet ? 2 : showOverlap && hasOverlap ? 1 : 0;
  const overlapDisabled = areCourses || (!hasOverlap && sampleUniverse.length === 0);
  const stepOverlap = () => {
    if (rung === 2) {
      setSelectionForSampleSet(null);
      setCandidatesState({ showOverlap: false });
    } else if (rung === 1 || !hasOverlap) {
      // Opening the picker seeds it with the set the bars are already on, so the first thing
      // the operator sees is the reading they were reading — editable.
      setSelectionForSampleSet(overlap?.sample_ids ?? sampleUniverse);
      setCandidatesState({ showOverlap: true });
    } else {
      setCandidatesState({ showOverlap: true });
    }
  };
  // What the NEXT press does, per rung. With no served reading, rung 1 does not exist and the
  // first press goes to the picker — so it must not promise a series the campaign cannot draw,
  // and it says WHY there is none, or the card reads as still loading when nothing is pending.
  const overlapNext = [
    hasOverlap
      ? "Read C0 and every winner since on the one set of cells all of them answered. The bars beside it stay on each candidate's own cells."
      : "Pick a set of cells and read every candidate that answered all of it on that one basis. There is no reading to show yet: the adopted line is still C0 alone, and a second member arrives with the first round that promotes a winner — a held round leaves nothing to read C0 against.",
    "Choose which cells the overlap bars are read on — any round's set, or your own pick.",
    "Hide the overlap bars and drop the picked set.",
  ];

  // What the chart is currently painting, and the half of it the header does not already
  // name. `metric == null` IS "has no chip" — the registry answers it, so this row can never
  // fall out of step with the chips above it.
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

  // Rides the menu label as a count, never as a gate: a disabled control cannot tell you C0
  // was replayed, which is the answer the origin is most often opened for.
  const cacheHitCount = useMemo(
    () => views.filter((v) => (v.cached_samples ?? 0) > 0).length,
    [views],
  );

  // One quiet disclosure, not a view switch: it appends the forest below, it never
  // takes the bars away. Carries the descendant count, because the forest is the
  // only thing that can draw siblings — so "there are 3" belongs on the control
  // that reveals them.
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
      // ONE short row, and only what the operator reads constantly: which number
      // am I looking at (Metric), and the escape hatches (⋯, copy). Everything
      // rare — the lens, the ablation, the fixed sample set, the θ explainer,
      // stub cleanup — folds into the menu rather than onto a second row. The
      // VIEW switch isn't here at all: it lives down beside the tree it switches.
      title={
        <Toolbar className="cand-toolbar">
          {/* Run-bars mode names the viewed candidate and is the way back up a tier. */}
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
          {/* WHICH BARS — four facets of one question, hence `joined`. The first three are the
              metric axis, which also names every dendrogram node; `∩` is not a fourth number but
              `accuracy` on a fixed basis, so it keeps its own ink and stays out of `metrics`.
              Each chip's underline wears its ink, making this group the legend for what it
              switches. Display only — the engine gates on θ whatever is lit here. */}
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
            {/* `∩` — the overlap IS the intersection: the cells every drawn candidate
                answered. Notation, like the three beside it. */}
            <Chip
              icon
              on={rung > 0}
              // The ink of what the press put ON SCREEN, never of what the chip names: teal for
              // the bars, the picker's own colour once rung 2 opens it.
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
                {/* Lens: re-project the record under an alternative criterion and
                    mark where it would have forked the realized lineage. Backend
                    projection; this only picks which served overlay renders.
                    Disabled while the scoring mask drives the lens itself. */}
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
                <MenuCheck
                  on={maskOpen}
                  onClick={() => setScoringMask({ open: !maskOpen })}
                  title="Pick evaluators and reweight them to recompute every score under a criterion you choose."
                >
                  Scoring mask
                </MenuCheck>
                {/* Never disabled — the origin is normally the cached one, so greying out
                    when only C0 was replayed hides the case this is opened for. */}
                <MenuCheck
                  on={showCache}
                  onClick={() => setCandidatesState({ showCache: !showCache })}
                  title="Show how much of each candidate's samples were replayed from the archive instead of measured."
                >
                  Loaded from cache{cacheHitCount > 0 ? ` · ${cacheHitCount}` : ""}
                </MenuCheck>
                <MenuSep />
                {/* A searchpoint is picked where it is being LOOKED AT — the lit bar, the
                    dendrogram node and the forest stub all write one selection slot, so the
                    affordance rides that slot rather than being drawn three times. Compare then
                    reads it off the shell-level set, which outlives this cycle. */}
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
                {/* The θ explainer — read once, then never again, so it lives here
                    rather than owning a permanent toolbar button. The ruler locks
                    once warm, so the latest round carries the cycle's model. */}
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
        {/* On the θ it invalidates, not behind the `⋯` disclosure: a reading that is not ability
            renders every number and raises nothing, so the screen has to say so unprompted. */}
        {!areCourses && <ThetaCaveatNotice ability={history.at(-1)?.ability ?? null} />}
        {sampleSet && !areCourses && (
          <SampleSetControl rounds={history} overlap={overlap} unit={unit} />
        )}
        {/* Legend + chart + genealogy wrapped so they share one width — the
            dendrogram's x-alignment depends on sitting in the same box as the
            canvas it hangs under. */}
        <div className="fitness-chart-wrap">
          {/* Only the channels with no chip. A metric restating `headlineMetricLabel` under
              its own lit, ink-matched chip is a second row answering a question the header
              already answered — so on the default view this is empty and does not render. */}
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
          {/* The forest toggle lives HERE, not in the header — it reveals the tree,
              so it sits with the tree. Tiny and quiet on purpose: most campaigns
              have no siblings at all, so it has nothing to show and nobody should
              be paying header width for it. */}
          <div className="cand-tree-row">
            {/* Parked on a candidate, the bars are sibling courses — no descent to draw. */}
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
            // No samples field here: the chip strip above owns that axis, with per-round picks and
            // coverage this input cannot show. Two writers on one fact is what the card avoids.
            summary={<FitnessRankSummary views={views} criterion={activeLens != null} />}
          />
        )}
        {/* The preview's other half: the round it names is the round a fork carrying it is cut
            at, so the two sit in one box rather than in two surfaces that have to agree. */}
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
