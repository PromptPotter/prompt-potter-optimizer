"use client";
import { useEffect, useMemo } from "react";
import {
  WHATIF_INLINE_META,
  buildRows,
  whatifIdentifiersInFormula,
  weightsFromFormula,
  type Row,
} from "./meta";
import { FitnessChart } from "./FitnessChart";
import { setFitnessState, useFitnessState } from "./fitness-store";
import { CardFrame, CopyButton } from "@/components/ui";
import {
  liveL1Candidates,
  type LiveCandidate,
} from "@/lib/poll";
import type { RoundSummary } from "@/lib/api/types";
import { useSelection } from "@/lib/SelectionContext";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useEffectiveRound } from "@/lib/hooks/useEffectiveRound";
import { FitnessFormulaEditor } from "./FitnessFormulaEditor";
import { fetchDiagnosticRuns, type DiagnosticRunRecord } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useFetch } from "@/lib/hooks/useFetch";
import { useChartedRoundDocs } from "@/lib/hooks/useChartedRoundDocs";
import { roundHasCandidates, sortedRounds } from "@/lib/derivations";
import { useWorkspace } from "@/lib/workspace";
import { useFitnessBars } from "./useFitnessBars";
import { SampleSetControl } from "./SampleSetControl";
import { measuredUniverse } from "@/lib/sample-set";
import { useLineageOverlay, divergenceRoundsFor } from "@/lib/lineage-overlay";

export function FitnessPanel() {
  // Self-sourced: live snapshot from the cycle stream, (campaignId, cycleId)
  // from the workspace. `cycleId` scopes the one-shot evaluator-seed.
  const { dash } = useDashboard();
  const { campaignId, cycleId } = useWorkspace();
  // Shared candidate selection — driving any of {fitness bar, lineage stub}
  // sets this context slot; the other surface(s) re-render highlighted.
  // FitnessChart resolves selectedKey → bar index by matching `bar.key`
  // against the SelectedCandidate's {round, candidate_id}. Aliased away
  // from the evaluator-set `selected` already in this component.
  const {
    candidate: selectedCandidate,
    setSelectionForCandidate,
    sampleSet,
    setSelectionForSampleSet,
  } = useSelection();
  // Default view: chart-only (one bar per candidate = accuracy). The
  // composite chip pairs the formula-weighted score; the what-if chip
  // opens the ablation widget below the chart. State lives in a module
  // store so swapping tabs (New Job ↔ View Results) preserves chip and
  // selection state across the FitnessPanel unmount.
  //
  // Seeding is scoped to the cycle: `seededForCycle` records which cycle
  // the current `selected` set was seeded for. When the operator binds a
  // fresh cycle, the panel re-seeds against that cycle's formula rather
  // than inheriting the prior cycle's picks.
  const { showComposite, showWhatIf, selected, weights, seededForCycle } = useFitnessState();
  const seeded = seededForCycle != null && seededForCycle === cycleId;
  const setShowComposite = (v: boolean | ((p: boolean) => boolean)) =>
    setFitnessState({ showComposite: typeof v === "function" ? v(showComposite) : v });
  const setShowWhatIf = (v: boolean | ((p: boolean) => boolean)) =>
    setFitnessState({ showWhatIf: typeof v === "function" ? v(showWhatIf) : v });
  const setSelected = (s: Set<string>) => setFitnessState({ selected: s });
  const setWeight = (name: string, w: number) =>
    setFitnessState({ weights: { ...weights, [name]: w } });

  const meta = WHATIF_INLINE_META;

  // ── 1. In-flight candidates from the live dashboard. Memoized on `dash`
  // so identity is stable across polls (and across no-op 304 ticks): the
  // downstream Set chain (realApplicable→viewApplicable→inActive) only
  // rebuilds when `dash` actually changes, so the seed + prune guards below
  // converge instead of looping setState every render.
  const inflightCandidates: LiveCandidate[] = useMemo(
    () => liveL1Candidates(dash),
    [dash],
  );
  // The round in view — selected pick, else live — from the shared resolver, so
  // the chip agrees with the samples + score-frequency cards (the bars below
  // still span every round; this chip is the active round, not a scope on them).
  const { round: effectiveRound } = useEffectiveRound();
  const currentRound = effectiveRound ?? 0;

  // ── 2. Completed-round summaries from `dash.rounds[]` — sole source
  // of truth for historical bars. The projection accumulates these at
  // `round:display` so the chart never has to stitch live + finalized
  // round-file fetches.
  // Key on `dash?.rounds` (the only slice `sortedRounds` reads), not on `dash`,
  // so unrelated per-poll dash mutations don't re-sort (render-cost guard).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const history: RoundSummary[] = useMemo(() => sortedRounds(dash), [dash?.rounds]);

  // ── 2b. Diagnostic-run records — one per `python -m promptpotter verify`
  // invocation, persisted at archive/diagnostic_runs/*.json. Fetched per
  // cycle switch; not polled (the panel never auto-refreshes verify state,
  // re-run verify + reload for a fresh red bar). Filtered to runs whose
  // (source_campaign, source_cycle) match the unit currently in view, then
  // keyed by source_label so the bars-assembly memo can attach diag data
  // to the matching candidate.
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
      for (const k of Object.keys(c.stats?.evaluators ?? {})) set.add(k);
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

  // The realized composite formula in effect — top-level when present, else the
  // first candidate's. Drives both `inActive` (which evaluators it references) and
  // the what-if weight seed (their coefficients).
  const compositeFormula = useMemo(() => {
    const top = (dash as { composite_fitness_formula?: string | null } | null)
      ?.composite_fitness_formula;
    if (top) return top;
    for (const c of inflightCandidates) {
      if (c.stats?.composite_fitness_formula) return c.stats.composite_fitness_formula;
    }
    return null;
  }, [dash, inflightCandidates]);

  const inActive = useMemo(() => {
    let parsed: Set<string> | null = compositeFormula
      ? whatifIdentifiersInFormula(compositeFormula)
      : null;
    if (parsed == null) {
      parsed = new Set<string>();
      for (const c of inflightCandidates) {
        for (const k of Object.keys(c.stats?.evaluators ?? {})) parsed.add(k);
      }
    }
    // Drop phantom tokens (`min`, `weight`, …) parsed from formula arithmetic so the bars-memo equality short-circuit is honest.
    const out = new Set<string>();
    for (const k of parsed) if (viewApplicable.has(k)) out.add(k);
    return out;
  }, [compositeFormula, inflightCandidates, viewApplicable]);

  const rows = useMemo(() => {
    const built = isPrestaging
      ? meta.map<Row>((m) => ({
          displayName: m.name,
          registryName: m.name,
          applicable: true,
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
  }, [meta, realApplicable, inActive, selected, isPrestaging]);

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
    setFitnessState({
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
    setFitnessState({ selected: next });
  }, [seeded, viewApplicable, selected]);

  const toggle = (name: string) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSelected(next);
  };

  // Decorate the canonical candidate spine with what-if + diag overlays.
  // The spine itself (origin + historical + in-flight, uniform ids and
  // labels) comes from `useRoundCandidates` — the same source
  // LineageTree reads. With one derivation the two surfaces cannot
  // disagree on count, labels, or selection target.
  // Fixed-sample-set mode (driven by the Sample-trajectory "Steps" view) needs
  // the per-(candidate, sample) hit matrix from each charted round's file —
  // fetched only while a set is active, so the default chart stays dash-only.
  const chartedRounds = useMemo(
    () => history.filter(roundHasCandidates).map((h) => h.round),
    [history],
  );
  const chartedDocs = useChartedRoundDocs(
    campaignId,
    cycleId,
    chartedRounds,
    sampleSet != null,
  );

  // The measured-sample universe the bars can be sliced over — used to seed the
  // set when the operator first turns the mode on. The chip strip + per-round
  // picks + trajectory drill all live in `SampleSetControl`.
  const sampleUniverse = useMemo(() => measuredUniverse(history), [history]);

  // The shared served overlay — its `lensValueByCandidate` is the What-If bar value
  // (R-36, never recomputed here), and its divergence facts drive the boundary below.
  const overlay = useLineageOverlay();
  const bars = useFitnessBars(
    diagByLabel,
    sampleSet,
    chartedDocs,
    dash,
    overlay.lensValueByCandidate,
    cycleId,
  );

  // Mask divergence boundary → the bar index where the active lens first parts
  // ways with the realized record. We read the shared served overlay; we map its
  // earliest divergent round for THIS cycle to the first bar at/after it, and the
  // chart draws a red divider at that bar's left edge. null whenever no mask is
  // active or nothing diverges.
  const divergenceBoundary = useMemo(() => {
    if (!overlay.maskActive) return null;
    const { points, subtree } = divergenceRoundsFor(overlay, cycleId);
    let firstRound = Infinity;
    for (const r of points) firstRound = Math.min(firstRound, r);
    for (const r of subtree) firstRound = Math.min(firstRound, r);
    if (!Number.isFinite(firstRound)) return null;
    const idx = bars.findIndex((b) => b.round != null && b.round >= firstRound);
    return idx >= 0 ? idx : null;
  }, [overlay, cycleId, bars]);

  return (
    <CardFrame
      className={`fitness-card${showWhatIf ? " whatif-open" : ""}`}
      title={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span>Per-candidate fitness</span>
          <span className="badge" title="Bars span every round, origin first; chip = the round in view (selected, else live)">R{currentRound}</span>
        </span>
      }
      actions={
        <div className="fitness-toggles" role="group" aria-label="Score views">
          <button
            type="button"
            className={`fitness-chip${showComposite ? " on" : ""}`}
            aria-pressed={showComposite}
            onClick={() => setShowComposite((v) => !v)}
            title="Pair the formula-weighted composite score alongside the accuracy bar."
          >
            Composite
          </button>
          <button
            type="button"
            className={`fitness-chip${showWhatIf ? " on" : ""}`}
            aria-pressed={showWhatIf}
            onClick={() => setShowWhatIf((v) => !v)}
            title="Open the what-if ablation: pick evaluators to recompute scores client-side."
          >
            What-If
          </button>
          <button
            type="button"
            className={`fitness-chip${sampleSet ? " on" : ""}`}
            aria-pressed={sampleSet != null}
            onClick={() =>
              setSelectionForSampleSet(sampleSet ? null : sampleUniverse)
            }
            disabled={sampleUniverse.length === 0}
            title="Recompute every bar over one fixed set of samples so candidates compare on the same basis. Toggle samples below, or seed a set by clicking a square in the Sample-trajectory Series view."
          >
            Sample set{sampleSet ? ` · ${sampleSet.length}` : ""}
          </button>
          <CopyButton data={bars} title="Copy all candidate fitness as JSON" />
        </div>
      }
    >
      <div className="fitness-body">
        {sampleSet && <SampleSetControl rounds={history} />}
        {/* Legend + chart wrapped so the legend sits over the chart's
            width specifically — when What-If opens and the card stretches,
            the legend stays anchored above the bars instead of drifting
            to the centre of the wider card. */}
        <div className="fitness-chart-wrap">
          {(showComposite || showWhatIf) && (
            <div className="fitness-legend">
              <span><span className="dot accuracy" />accuracy</span>
              {showComposite && <span><span className="dot composite" />composite</span>}
              {showWhatIf && <span><span className="dot whatif" />what-if</span>}
            </div>
          )}
          <FitnessChart
            bars={bars}
            showComposite={showComposite}
            showWhatIf={showWhatIf}
            divergenceBoundary={divergenceBoundary}
            selectedKey={
              selectedCandidate
                ? bars.find(
                    (b) =>
                      b.round === selectedCandidate.round &&
                      b.candidateId === selectedCandidate.candidate_id,
                  )?.key ?? null
                : null
            }
            onSelect={(bar) => {
              if (!bar || !bar.candidateId || bar.round == null) {
                setSelectionForCandidate(null);
                return;
              }
              // Atomic candidate+round write — the round axis follows
              // the bar's round so a click in either surface re-anchors
              // the other on the same tick.
              setSelectionForCandidate({
                round: bar.round,
                candidate_id: bar.candidateId,
                label: bar.label,
                accuracy: bar.accuracy,
                is_winner: !!bar.isWinner,
              });
            }}
          />
        </div>
        {showWhatIf && (
          <FitnessFormulaEditor
            rows={rows}
            selected={selected}
            inActive={inActive}
            weights={weights}
            bars={bars}
            onToggle={toggle}
            onWeight={setWeight}
          />
        )}
      </div>
    </CardFrame>
  );
}
