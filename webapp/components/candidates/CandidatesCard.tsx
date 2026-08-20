"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  buildRows,
  whatifIdentifiersInFormula,
  weightsFromFormula,
  type Row,
} from "./meta";
import { EVALUATOR_META } from "@/lib/api/types.generated";
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
  HEADLINE_METRICS,
  headlineMetricLabel,
  nodeKeyOf,
  panelCellLabel,
  pathOf,
  sortedRounds,
  type HeadlineMetric,
} from "@/lib/derivations";
import { isSelectedCandidate } from "@/lib/types";
import { encodeCyclePath } from "@/lib/ids";
import { useWorkspace } from "@/lib/workspace";
import { useViewMemory } from "@/lib/view-memory";
import { useLineage } from "./useLineage";
import { SampleSetControl } from "./SampleSetControl";
import { measuredUniverse } from "@/lib/sample-set";
import { useViewedLineage, divergenceRoundsFor } from "@/lib/lineage";
import { useConnector } from "@/lib/hooks/useConnector";
import { targetNodeIds } from "@/lib/terms";
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
// The metric glyphs. `%` and `∑` and `θ` ARE the icons — they're the notation the
// numbers are already written in, so drawing a picture of them would be a
// translation nobody asked for.
const METRIC_GLYPH: Record<HeadlineMetric, string> = {
  accuracy: "%",
  composite: "∑",
  ability: "θ",
};

// The latest `verify` run for a candidate, in the shape the chart paints.
function diagView(
  d: DiagnosticRunRecord | undefined,
): { accuracy: number; workspaceN: number; samplesAdded: number } | undefined {
  return d
    ? { accuracy: d.workspace_accuracy, workspaceN: d.workspace_n, samplesAdded: d.samples_added }
    : undefined;
}

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
  const {
    campaignId,
    cycleId,
    leafCycleId,
    viewedPath,
    viewedCandidateId,
    selectCyclePath,
  } = useWorkspace();
  // Per-campaign view memory — the card records what the operator arranges here.
  const { recordView } = useViewMemory();
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
    showWhatIf,
    selected,
    weights,
    seededForCycle,
    showCache,
  } = useCandidatesState();
  const seeded = seededForCycle != null && seededForCycle === cycleId;
  const setShowWhatIf = (v: boolean) => setCandidatesState({ showWhatIf: v });
  const setSelected = (s: Set<string>) => setCandidatesState({ selected: s });
  const setWeight = (name: string, w: number) =>
    setCandidatesState({ weights: { ...weights, [name]: w } });

  const meta = EVALUATOR_META;

  // Pipeline shape from the connector view. A single-node (llm_only) pipeline has
  // no candidate_source / ranker / cache node, so the node-type-bound evaluators
  // (source_recall / candidate_recall / cache_hit_rate) can never apply — they must
  // not surface as live tiles before the first round lands. Mirrors
  // PipelineSchema.is_single_node (targetNodeIds drops the io ports).
  const cv = useConnector();
  const singleNode = targetNodeIds(cv.view).length <= 1;

  // ── 1. In-flight candidates from the live dashboard. Memoized on `dash`
  // so identity is stable across polls (and across no-op 304 ticks): the
  // downstream Set chain (realApplicable→viewApplicable→inActive) only
  // rebuilds when `dash` actually changes, so the seed + prune guards below
  // converge instead of looping setState every render.
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

  // ── 3. The applicable evaluator set unions every candidate we plot. The
  // origin row has no evaluators; in-flight stats and historical
  // round-summary candidates both carry the full dict.
  const realApplicable = useMemo(() => {
    const set = new Set<string>();
    for (const c of inflightCandidates) {
      for (const k of Object.keys(c.evaluators)) set.add(k);
    }
    for (const h of history) {
      for (const c of h.candidates) {
        for (const k of Object.keys(c.evaluators)) set.add(k);
      }
    }
    return set;
  }, [inflightCandidates, history]);

  const isPrestaging = realApplicable.size === 0;

  const viewApplicable = useMemo(() => {
    if (!isPrestaging) return realApplicable;
    const set = new Set<string>();
    for (const m of meta) set.add(m.name);
    return set;
  }, [isPrestaging, realApplicable, meta]);

  // The realized composite formula in effect. Drives both `inActive` (which evaluators it
  // references) and the what-if weight seed (their coefficients). One field: the per-candidate
  // copy this used to fall back to was the SAME string, stamped onto every row of every round
  // from this very value — a second channel carrying one fact, and it is gone from the wire.
  const compositeFormula =
    (dash as { composite_fitness_formula?: string | null } | null)?.composite_fitness_formula ??
    null;

  const inActive = useMemo(() => {
    let parsed: Set<string> | null = compositeFormula
      ? whatifIdentifiersInFormula(compositeFormula)
      : null;
    if (parsed == null) {
      parsed = new Set<string>();
      for (const c of inflightCandidates) {
        for (const k of Object.keys(c.evaluators)) parsed.add(k);
      }
    }
    // Drop phantom tokens (`min`, `weight`, …) parsed from formula arithmetic so the assembly-memo equality short-circuit is honest.
    const out = new Set<string>();
    for (const k of parsed) if (viewApplicable.has(k)) out.add(k);
    return out;
  }, [compositeFormula, inflightCandidates, viewApplicable]);

  const rows = useMemo(() => {
    const built = isPrestaging
      ? meta.map<Row>((m) => ({
          displayName: m.name,
          registryName: m.name,
          // Shape-agnostic evaluators (node_type == null) always apply; a
          // node-type-bound one applies pre-staging only if the pipeline could
          // carry that node — never on a single-node llm_only run.
          applicable: m.node_type == null || !singleNode,
          description: m.description,
          direction: m.direction,
        }))
      : buildRows(meta, realApplicable);
    const bucketOf = (r: Row) => {
      if (!r.applicable) return 3;
      if (inActive.has(r.displayName)) return 0;
      if (selected.has(r.displayName)) return 1;
      return 2;
    };
    return built.slice().sort((a, b) => bucketOf(a) - bucketOf(b));
  }, [meta, realApplicable, inActive, selected, isPrestaging, singleNode]);

  // Render-phase seed of the metric axis, once per cycle. Two bars per candidate by
  // default: accuracy (a candidate is rarely bad on it, and it's the universal read)
  // PLUS the campaign's ACTIVE metric — the served `CampaignConfig.headline_metric`,
  // the one the loop actually follows (usually θ). Composite stays hidden unless it is
  // the active metric. Gated on `dash`: on the first poll the field isn't there yet,
  // and seeding without it would ignore the campaign's own choice. θ is offered, never
  // forced — the engine always GATES on θ regardless of what's displayed here.
  if (cycleId && dash && metricsSeededForCycle !== cycleId) {
    setCandidatesState({
      metrics: new Set<HeadlineMetric>(["accuracy", dash.headline_metric ?? "accuracy"]),
      metricsSeededForCycle: cycleId,
    });
  }

  // Render-phase seed: when the cycle binds applicable evaluators for the
  // first time (or the cycle changes), seed `selected` from the formula's
  // inActive set so the operator opens to "what's actually scored" as the
  // default. `seededForCycle` (the store flag read as `seeded`) is the single
  // guard: it fires the seed once per cycle and — unlike a component-local flag
  // — persists across the New Job ↔ View Results remount, so a tab swap doesn't
  // re-seed. The store write below flips `seeded` true on the next render
  // (`useSyncExternalStore`, tear-free), so the guard converges after one fire.
  // The render loop is held off upstream — `inflightCandidates` is a stable
  // ref, so the Set chain feeding this condition doesn't churn every render.
  // Bail when `cycleId == null` (no active campaign yet).
  if (cycleId && viewApplicable.size > 0 && !seeded) {
    const seed = new Set<string>();
    for (const r of rows) {
      if (r.applicable && inActive.has(r.displayName)) seed.add(r.displayName);
    }
    // Seed each evaluator's slider from its realized composite coefficient, so the
    // What-If opens ≈ the realized criterion and reweighting reveals divergence.
    setCandidatesState({
      selected: seed,
      weights: weightsFromFormula(compositeFormula),
      seededForCycle: cycleId,
    });
  }

  // Prune: when the applicable evaluator set shrinks (e.g. a node was
  // disabled and its evaluators dropped out), remove selections that fell
  // off. The effect only removes from `selected`, never adds, so it
  // terminates after one render (next pass: drop.length === 0).
  useEffect(() => {
    if (!seeded) return;
    const drop = [...selected].filter((n) => !viewApplicable.has(n));
    if (!drop.length) return;
    const next = new Set(selected);
    for (const n of drop) next.delete(n);
    setCandidatesState({ selected: next });
  }, [seeded, viewApplicable, selected]);

  const toggle = (name: string) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSelected(next);
  };

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

  // The mask re-scores a candidate's measured ROWS. A course is a run, not a scored row, so
  // a view whose bars are courses (an L4 candidate's inner cells) has nothing to slice — the
  // server decorates candidates only. Reading `sample_set_accuracy` off a course yielded null,
  // which `started` then rendered as "never ran": the one lie this card must not tell. Bars
  // stay on their own measured value here and the control below says why. Children strictly
  // alternate, so this is all-or-nothing and the basis never mixes within one chart.
  const barsAreCourses = useMemo(
    () => (viewedNode?.children ?? []).some((n) => n.kind === "course"),
    [viewedNode],
  );

  const views = useMemo<CandidateView[]>(() => {
    const sliced = sampleSet != null && !barsAreCourses;
    // ONE half per bar, ALL-OR-NOTHING, chosen once here: the tree, unless it has no
    // measurement for this candidate yet. That single condition is the whole rule now — the
    // ledger mints a candidate before it measures one, but only snapshots the score at
    // completion, so a bar mid-scoring is the one thing the tree cannot answer.
    //
    // It used to also prefer the live half for the whole of an OPEN round, because the crown
    // rode the round's CLOSE record while `elect_round_winner` decides at the end of SCORING —
    // a whole `l1_critique` call earlier. The election has its own ledger record at its own
    // coordinate now, so the tree crowns when the election does and that window is gone.
    return (viewedNode?.children ?? []).map<CandidateView>((n, i) => {
      const isCourse = n.kind === "course";
      // A course shows what it reached, else what it started from. A cut that broke before
      // measuring anything has no number and must render blank, never as its origin's.
      const own = isCourse ? (n.best_accuracy ?? n.origin_accuracy) : n.accuracy;
      const live = isCourse ? undefined : inflightByLabel.get(n.label);
      const useLive = live != null && own == null;
      // The chosen half. Every measured number below reads off THIS, so a bar and its whisker
      // can never come from two different polling clocks.
      const m = useLive ? live : n;
      // Slice mode reads the SERVED scorer-faithful value. Election aggregates can't be
      // re-sliced per sample, so they are suppressed rather than shown on a different basis
      // than the bar beside them.
      const accuracy = sliced
        ? n.sample_set_accuracy
        : isCourse
          ? (own ?? null)
          : (m.accuracy ?? null);
      const label = isCourse ? (n.task ? panelCellLabel(n.task) : n.dataset_name) : n.label;
      return {
        key: nodeKeyOf(n),
        round: n.round ?? 0,
        idx: i,
        candidate_id: n.id,
        label,
        accuracy,
        composite: sliced || isCourse ? null : (m.composite_fitness ?? null),
        theta: sliced ? null : (m.theta ?? null),
        theta_se: sliced ? null : (m.theta_se ?? null),
        // From the same row as the bar above it, whichever half that was.
        meanFitnessCiLo: sliced ? null : (m.mean_fitness_ci_lo ?? null),
        meanFitnessCiHi: sliced ? null : (m.mean_fitness_ci_hi ?? null),
        // Inherited from `CandidateRow` and unset here because NOTHING PLOTS A FLOOR ON A BAR,
        // and a lift interval is not a bar geometry either. The matched origin and the blocked
        // lift are per-candidate numbers the inspector renders for the one row it selected
        // (`ScoringInspector`, off `roundCandidates`), so filling them in here would put a second
        // writer on a chart nothing reads them from. The election record DOES carry all five —
        // `ScoreboardRow` and `RoundSummaryCandidate` both serve them — this half simply has no
        // use for them; `/tree` is a genealogy, not a verdict surface.
        matchedParentAccuracy: null,
        matchedParentComposite: null,
        matchedParentLift: null,
        matchedParentLiftCiLo: null,
        matchedParentLiftCiHi: null,
        evaluators: n.evaluators,
        is_winner: m.is_winner ?? false,
        n_samples: sliced ? n.sample_set_n : (m.scored_samples ?? null),
        n_expected: sliced ? (sampleSet?.length ?? null) : (m.expected_samples ?? null),
        cached_samples: sliced ? null : (m.cached_samples ?? null),
        source: useLive ? "inflight" : "history",
        whatif: sliced ? null : n.lens_value,
        // Ranks follow their values exactly: suppressed on the same two conditions, or a
        // bar would carry a position in an ordering whose number it is not showing.
        compositeRank: sliced || isCourse ? null : n.composite_rank,
        whatifRank: sliced ? null : n.lens_rank,
        started: accuracy != null,
        // SERVED, not inferred from whether the round has closed. `is_winner: false` says
        // nothing on its own — a round that HELD crowned nobody and every bar in it reads the
        // same as one still scoring — and the browser used to guess between them off
        // `dash.rounds[]`, which reported every held round as undecided for the rest of the run.
        electionPending: !isCourse && !n.election_held,
        diag: diagView(diagByLabel.get(label)),
        // Suppressed while sliced or on a course, exactly like the other served aggregates: this
        // is a rate over the LINE's own set, and re-basing the bars onto a different one leaves
        // it describing cells the chart is no longer showing.
        overlapAccuracy: sliced || isCourse ? null : (overlapByCandidate.get(n.id)?.accuracy ?? null),
        overlapN: sliced || isCourse ? null : (overlapByCandidate.get(n.id)?.total ?? null),
      };
    });
  }, [viewedNode, inflightByLabel, sampleSet, barsAreCourses, diagByLabel, overlapByCandidate]);

  // A fork's attempt is a course under the hood — the ⑂ marks lead there.
  const forkKeys = useMemo(
    () =>
      new Set(
        (viewedNode?.children ?? []).filter((n) => n.course_kind != null).map((n) => nodeKeyOf(n)),
      ),
    [viewedNode],
  );

  // Only what the BARS need from the lineage: the metric they paint, the fork
  // marks on the dendrogram, and the descendant count on the forest toggle. The
  // tree itself — forests, overlays, cleanup — moved out with `ForestCard`.
  const { metric, forkedFrom, expanded, setShowForest, totalDescendants } = useLineage({
    campaignId,
    cycleId,
    path: viewedPath,
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
  // with that cycle expanded and in view. The bars stay put.
  //
  // `expanded` holds LANE KEYS (`nodeKeyOf` = `{encoded path}|{id}`), which is what
  // `forest-layout::layout` matches on. This used to add the raw cycle id, so the set grew
  // a key the layout could never match and the click silently did nothing but open an
  // unexpanded forest. Navigation likewise rides the node's own path.
  const onFreeHierarchy = useCallback(
    (course: LineageNode) => {
      const next = new Set(expanded).add(nodeKeyOf(course));
      setCandidatesState({ showForest: true, expanded: next });
      recordView(campaignId, { showForest: true, expandedLanes: [...next] });
      selectCyclePath(pathOf(course), null);
    },
    [expanded, campaignId, recordView, selectCyclePath],
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
        showWhatIf && "whatif-open",
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
          {/* ONE metric axis for the whole card: it picks the bar series AND the
              number painted on every node, in both views. Multi-select — Acc+Comp
              plots them side by side. The engine always GATES on θ; this is pure
              display, seeded from the campaign default so θ is offered, never forced.
              It stays in the header because "which number is this?" is the question
              you ask on every glance. */}
          {/* `joined`: these are three facets of ONE axis, not three switches —
              one frame, no borders between them, the on ones underlined. */}
          <ChipGroup label="Metric" joined>
            {HEADLINE_METRICS.map((m) => (
              <Chip
                key={m.id}
                icon
                on={metrics.has(m.id)}
                ariaLabel={headlineMetricLabel(m.id)}
                title={m.title}
                onClick={() => toggleMetric(m.id)}
              >
                {METRIC_GLYPH[m.id]}
              </Chip>
            ))}
          </ChipGroup>
          <ToolbarSpacer />
          <Menu
            renderTrigger={({ open, toggle }) => (
              <Chip
                icon
                on={
                  open ||
                  lensActive ||
                  showWhatIf ||
                  showCache ||
                  (sampleSet != null && !barsAreCourses)
                }
                ariaLabel="More candidate options"
                title="Lens, What-If, sample set, cache overlay, and the θ explainer"
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
                  on={showWhatIf}
                  onClick={() => setShowWhatIf(!showWhatIf)}
                  title="Pick evaluators and reweight them to recompute every score under a criterion you choose."
                >
                  What-If ablation
                </MenuCheck>
                <MenuCheck
                  on={sampleSet != null && !barsAreCourses}
                  disabled={sampleUniverse.length === 0 || barsAreCourses}
                  onClick={() => setSelectionForSampleSet(sampleSet ? null : sampleUniverse)}
                  title={
                    barsAreCourses
                      ? "These bars are runs, not scored samples — pick a run to slice its candidates."
                      : "Recompute every bar over one fixed set of samples so candidates compare on the same basis."
                  }
                >
                  Fixed sample set{sampleSet && !barsAreCourses ? ` · ${sampleSet.length}` : ""}
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
                  <AbilityHelp model={history.at(-1)?.calibration_model ?? null} />
                )}
              </>
            )}
          </Menu>
          <CopyButton data={views} title="Copy all candidates as JSON" />
        </Toolbar>
      }
    >
      <div className="fitness-body">
        {sampleSet && !barsAreCourses && <SampleSetControl rounds={history} overlap={overlap} />}
        {/* Legend + chart + genealogy wrapped so they share one width — the
            dendrogram's x-alignment depends on sitting in the same box as the
            canvas it hangs under. */}
        <div className="fitness-chart-wrap">
          {/* `showCache` forces the legend on even at one metric: the dashed line rides the
              accuracy axis, so without a key 0.50 reads as a score. */}
          {(metrics.size > 1 || showWhatIf || showCache || overlap != null) && (
            <div className="fitness-legend">
              {metrics.has("accuracy") && (
                <span><span className="dot accuracy" />accuracy</span>
              )}
              {metrics.has("ability") && (
                <span><span className="dot ability" />ability θ</span>
              )}
              {metrics.has("composite") && (
                <span><span className="dot composite" />composite</span>
              )}
              {showWhatIf && <span><span className="dot whatif" />what-if</span>}
              {overlap != null && (
                <span title={`Every candidate on the winner trajectory, read on the same ${overlap.sample_ids.length} cells — the only pair of bars here that can be differenced`}>
                  <span className="dot overlap" />trajectory · {overlap.sample_ids.length}
                </span>
              )}
              {showCache && (
                <span title="Share of each candidate's scored panel that was replayed from the archive">
                  <span className="dash cached" />share from cache
                </span>
              )}
            </div>
          )}
          <FitnessChart
            views={views}
            metrics={metrics}
            showWhatIf={showWhatIf}
            showCache={showCache}
            divergenceBoundary={divergenceBoundary}
            inFlightIndex={inFlightIndex}
            selectedKey={selectedKey}
            onSelect={onSelect}
            onGeometry={onGeometry}
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
        {showWhatIf && !viewedCandidateId && (
          <WhatIfGrid
            rows={rows}
            selected={selected}
            inActive={inActive}
            weights={weights}
            views={views}
            onToggle={toggle}
            onWeight={setWeight}
          />
        )}
      </div>
    </CardFrame>
  );
}
