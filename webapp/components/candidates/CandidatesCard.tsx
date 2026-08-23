"use client";
import { useCallback, useMemo, useState, type CSSProperties } from "react";
import { activeSeries, metricInkToken, type SeriesCtx } from "./series";
import { FitnessChart, type PlotGeometry, geomEqual } from "./FitnessChart";
import { DendrogramStrip } from "./DendrogramStrip";
import { AbilityHelp } from "./AbilityInfo";
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
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { WhatIfGrid } from "./WhatIfGrid";
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
import { useWhatIf } from "./useWhatIf";
import { SampleSetControl } from "./SampleSetControl";
import { measuredUniverse } from "@/lib/sample-set";
import { useViewedLineage, divergenceRoundsFor } from "@/lib/lineage";
import { cx } from "@/lib/cx";
import type { CandidateView } from "@/lib/types";

// The candidates card — this cycle's population and its ancestry, in one surface.
//
// The bars and the dendrogram under them ride the SAME flat candidate spine
// (`roundCandidates` → C0, C1.1, C1.2, C2.1 …) and the bar chart's x categories ARE
// that spine — so the tree shares the bars' x-axis exactly, which is why it belongs
// beneath them, in their box. Everything in this card is bound to that alignment.
//
// The multi-cycle FOREST is deliberately NOT in here — it lives in `ForestCard`.
// It shares no axis with the bars (it is a cladogram of cycles on its own
// round-column grid), so binding it to this card's geometry bought nothing and
// cost it the width and height it actually wants. The quiet toggle beside the
// dendrogram opens it; that toggle is the only trace of it here.
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

  const {
    showForest,
    metrics,
    metricsSeededForCycle,
    showTrajectory,
    trajectorySeededForCycle,
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

  // ── 2. Completed-round summaries from `dash.rounds[]` — sole source
  // of truth for historical bars. The projection accumulates these at
  // `round:display` so the chart never has to stitch live + finalized
  // round-file fetches.
  // Key on `dash?.rounds` (the only slice `sortedRounds` reads), not on `dash`,
  // so unrelated per-poll dash mutations don't re-sort (render-cost guard).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const history: RoundSummary[] = useMemo(() => sortedRounds(dash), [dash?.rounds]);

  // ── 2b. Diagnostic-run records — one per `python -m promptpotter verify`
  // invocation, persisted at diagnostics/runs/*.json. Fetched per
  // cycle switch; not polled (the card never auto-refreshes verify state,
  // re-run verify + reload for a fresh red bar). Filtered to runs whose
  // (source_campaign, source_cycle) match the unit currently in view, then
  // keyed by source_label so the assembly memo can attach diag data to the
  // matching candidate.
  // Gate on a confirmed session — diagnostic-runs is workspace-scoped and
  // 401s for anon; `null` fetcher means useFetch fires nothing on the public
  // preview (frontend-surface-contract.md § I5), matching VerifyPane.
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

  // ── 3. The What-If ablation's evaluator machinery. Called UNCONDITIONALLY — see the hook's
  // own warning: `lib/lineage.tsx` reads the same store to build the tree's `?lens=` mask, so
  // a seed deferred to the panel's mount costs an unmasked refetch and one wrong frame.
  const whatIf = useWhatIf({ cycleId, dash, inflightCandidates, history });

  // Render-phase seed of the metric axis, once per cycle. Two bars per candidate by
  // default: accuracy (a candidate is rarely bad on it, and it's the universal read)
  // PLUS the campaign's ACTIVE metric — the served `CampaignConfig.headline_metric`,
  // the one the loop actually follows (usually θ). Composite stays hidden unless it is
  // the active metric. Gated on `dash`: on the first poll the field isn't there yet,
  // and seeding without it would ignore the campaign's own choice. θ is offered, never
  // forced — the engine always GATES on θ regardless of what's displayed here.
  if (cycleId && dash && metricsSeededForCycle !== cycleId) {
    setCandidatesState({
      metrics: new Set<HeadlineMetric>(["accuracy", electedMetric]),
      metricsSeededForCycle: cycleId,
    });
  }

  // The adopted line's shared reading, off the LATEST round that has one: the set drifts as
  // the line grows, and the newest round names the basis the bars are on now. Served
  // (`RoundSummary.overlap`) and only re-keyed here by candidate id — the browser computes no
  // rate. `overlap.sample_ids` doubles as the fixed-sample-set quick-pick below, so the strip
  // and this series are the same set by construction rather than by agreement.
  const overlap = useMemo(
    () => history.reduce<RoundSummary["overlap"]>((best, r) => r.overlap ?? best, null),
    [history],
  );
  const overlapByCandidate = useMemo(
    () => new Map((overlap?.members ?? []).map((m) => [m.candidate_id, m])),
    [overlap],
  );

  // The measured-sample universe the bars can be sliced over — used to seed the
  // set when the operator first turns the mode on. The chip strip + per-round
  // picks + trajectory drill all live in `SampleSetControl`.
  const sampleUniverse = useMemo(() => measuredUniverse(history), [history]);

  // The shared served overlay — the node's own `lens_value` is the What-If bar value
  // (R-36, never recomputed here), and its divergence facts drive the boundary below.
  const overlay = useViewedLineage();
  const { lens, setLens, maskActive, maskLabel, whatifActive } = overlay;

  // ── ONE RULE: the bars are the CHILDREN of the VIEWED node — the node the tree on the
  // left is parked on, and the same children it draws under it.
  //
  //   course viewed    → its timeline: every candidate on it, including the attempts its
  //                      forks contributed.
  //   candidate viewed → the courses that measured it (an L4 candidate's inner runs).
  //
  // The viewed node is `viewedPath` + `viewedCandidateId` — NAVIGATION, written only by the
  // tree. It is deliberately not `selectedCandidate` (INSPECTION, written by a bar click):
  // one slot for both makes the chart its own input, so clicking a bar re-plots it under
  // the cursor.
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
        diagByLabel,
        overlapByCandidate,
      }),
    [viewedNode, inflightByLabel, sampleSet, diagByLabel, overlapByCandidate],
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

  // The bar chart's plot geometry, published by its `xBridge` plugin — the one
  // thing the dendrogram needs in order to sit under the right bars. `geomEqual`
  // returning `prev` makes React bail out of the render entirely, which is what
  // keeps a window resize from ever reaching React (the fractions don't change;
  // only the px gutters could, and they don't under a pure width change).
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
      // A bar click INSPECTS. It never navigates — the chart must not move under the
      // cursor that clicked it, and the tree on the left is the only navigator. A course
      // bar (a fork, an inner run) is no exception: it is a measured thing with data
      // under it like any other, so it lights up and reports what it measured.
      //
      // Atomic candidate+round write — the round axis follows the candidate's round, so
      // picking one re-anchors the optimizer card on the round that produced it. The bars
      // plot `dash`, which is the LEAF's, so the leaf cycle produced this candidate.
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

  // Mask divergence boundary → the bar index where the active lens first parts
  // ways with the realized record. We read the shared served overlay; we map its
  // earliest divergent round for THIS cycle to the first bar at/after it, and the
  // chart draws a red divider at that bar's left edge. null whenever no mask is
  // active or nothing diverges.
  const divergenceBoundary = useMemo(() => {
    if (!overlay.maskActive) return null;
    // Parked on a candidate, the bars are sibling courses inside ONE round — no round
    // boundary to draw.
    if (viewedCandidateId) return null;
    const { points, subtree } = divergenceRoundsFor(overlay.index, viewedPath);
    let firstRound = Infinity;
    for (const r of points) firstRound = Math.min(firstRound, r);
    for (const r of subtree) firstRound = Math.min(firstRound, r);
    if (!Number.isFinite(firstRound)) return null;
    const idx = views.findIndex((v) => v.round >= firstRound);
    return idx >= 0 ? idx : null;
  }, [overlay.maskActive, overlay.index, viewedPath, views, viewedCandidateId]);

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

  const lensActive = lens !== "" && !whatifActive;

  // Read off the SERVED reading, not off the bars: slicing nulls every candidate's
  // `overlapAccuracy`, so asking the views would say "no trajectory" for the one state that
  // exists because of it.
  const hasTrajectory = overlap != null && !areCourses;

  // Seeded ON the render a reading first appears, so the default view is what it always was.
  // Latched per cycle, like the metric axis — the operator can then turn it off and it stays
  // off for that cycle.
  if (cycleId && overlap != null && trajectorySeededForCycle !== cycleId) {
    setCandidatesState({ showTrajectory: true, trajectorySeededForCycle: cycleId });
  }

  // ONE control, three rungs, because its two on-states are the same idea at two strengths:
  // SHOW the adopted line's reading on the cells all of it answered, then put EVERY bar on
  // those same cells. That is also why the series itself drops out at rung 2 — once all the
  // bars are read on that set, a separate "read on the shared set" series is the same bars
  // twice. The rung is DERIVED: the slice is `SelectionContext.sampleSet`, which already
  // owns "which cells are the bars on", so this reads it rather than keeping a second copy.
  const sliceOn = sampleSet != null && !areCourses;
  const rung = sliceOn ? 2 : showTrajectory && hasTrajectory ? 1 : 0;
  const trajectoryDisabled = areCourses || (!hasTrajectory && sampleUniverse.length === 0);
  const stepTrajectory = () => {
    if (rung === 2) {
      setSelectionForSampleSet(null);
      setCandidatesState({ showTrajectory: false });
    } else if (rung === 1 || !hasTrajectory) {
      // The cells the bars move onto are the trajectory's OWN where there are any, so the
      // series and the slice are the same set by construction rather than by agreement.
      setSelectionForSampleSet(overlap?.sample_ids ?? sampleUniverse);
      setCandidatesState({ showTrajectory: hasTrajectory });
    } else {
      setCandidatesState({ showTrajectory: true });
    }
  };
  // What the NEXT press does, per rung. With no trajectory reading yet, rung 1 does not
  // exist and the first press goes straight to the slice — so it must not promise a series
  // the campaign cannot draw, and it says WHY there is none: a run reads as "still loading"
  // otherwise, when in fact nothing is pending.
  const trajectoryNext = [
    hasTrajectory
      ? "Show the winner trajectory — every candidate on the adopted line, read on the one set of cells all of them answered."
      : "Re-base every bar onto one fixed set of cells so the candidates compare on the same basis. There is no trajectory to show yet: the adopted line is still C0 alone, and a second member arrives with the first round that promotes a winner — a held round leaves nothing to read C0 against.",
    "Re-base every bar onto those same cells, so all the candidates compare on one basis. The trajectory series folds in — at that point it would be the same bars twice.",
    "Back to each candidate's own measured subset.",
  ];

  // What the chart is currently painting, and the half of it the header does not already
  // name. `metric == null` IS "has no chip" — the registry answers it, so this row can never
  // fall out of step with the chips above it.
  const seriesCtx = useMemo<SeriesCtx>(
    () => ({
      metrics,
      showWhatIf: whatIf.open,
      showCache,
      showTrajectory: rung === 1,
      views,
      unit,
      electedMetric,
    }),
    [metrics, whatIf.open, showCache, rung, views, unit, electedMetric],
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
        whatIf.open && "whatif-open",
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
          {/* WHICH BARS — four facets of one question, hence `joined`. The first three are
              the metric axis, which also names every dendrogram node; the fourth is the
              trajectory, not a fourth number but `accuracy` on a fixed basis, which is why
              it keeps its own ink and stays out of `metrics`. Each chip's underline wears
              that ink, so this group IS the legend for what it switches and the row below
              carries only the chipless channels. Display only — the engine gates on θ
              whatever is lit here. */}
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
            {/* `∩` — the trajectory IS the intersection: the cells every candidate on the
                adopted line answered. Notation, like the three beside it. Teal ink, because
                it is not a fourth number but `accuracy` on a fixed basis, and it stays out
                of `metrics` for the same reason (that set names the dendrogram's node
                labels, and most candidates were never read on this set at all). */}
            <Chip
              icon
              on={rung > 0}
              // The chip wears the ink of what it put ON SCREEN, so it can never imply a series
              // the campaign has not drawn: teal while the trajectory series is up, and the
              // sample-set ink once every bar is on the slice — which is the colour of the panel
              // that press opens. A run that has held every round has NO second line member, so
              // its trajectory rung does not exist and teal must never appear for it.
              ink={rung === 2 ? "var(--color-new)" : "var(--color-overlap)"}
              disabled={trajectoryDisabled}
              ariaLabel={
                rung === 2
                  ? "Every bar is on one shared set of cells; press to leave"
                  : rung === 1
                    ? "Trajectory shown — press to put every bar on its cells"
                    : hasTrajectory
                      ? "Show the winner trajectory"
                      : "Compare every candidate on one fixed set of cells"
              }
              title={
                areCourses
                  ? "These bars are runs, not scored cells — open a run to compare its candidates."
                  : trajectoryNext[rung]
              }
              onClick={stepTrajectory}
            >
              ∩
            </Chip>
          </ChipGroup>
          <ToolbarSpacer />
          <Menu
            renderTrigger={({ open, toggle }) => (
              <Chip
                icon
                on={open || lensActive || whatIf.open || showCache}
                ariaLabel="More candidate options"
                title="Lens, What-If, cache overlay, and the θ explainer"
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
                    Disabled while What-If drives the lens itself. */}
                <MenuRadioGroup
                  label={whatifActive ? "Lens — driven by What-If" : "Lens"}
                  value={whatifActive ? "" : lens}
                  options={LENS_OPTIONS}
                  onChange={(v) => {
                    if (whatifActive) return;
                    setLens(v);
                    close();
                  }}
                />
                <MenuSep />
                <MenuCheck
                  on={whatIf.open}
                  onClick={() => whatIf.setOpen(!whatIf.open)}
                  title="Pick evaluators and reweight them to recompute every score under a criterion you choose."
                >
                  What-If ablation
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
                  <AbilityHelp model={history.at(-1)?.ability?.calibration_model ?? null} />
                )}
              </>
            )}
          </Menu>
          <CopyButton data={views} title="Copy all candidates as JSON" />
        </Toolbar>
      }
    >
      <div className="fitness-body">
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
            showWhatIf={whatIf.open}
            showTrajectory={rung === 1}
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
        {whatIf.open && !viewedCandidateId && (
          <WhatIfGrid whatIf={whatIf} views={views} />
        )}
      </div>
    </CardFrame>
  );
}
