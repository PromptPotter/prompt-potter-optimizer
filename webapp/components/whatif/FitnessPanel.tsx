"use client";
import { useEffect, useMemo } from "react";
import { WHATIF_INLINE_META, buildRows, setsEqual, whatifIdentifiersInFormula, type Row } from "./meta";
import { FitnessChart, type BarSlot } from "./FitnessChart";
import { setFitnessState, useFitnessState } from "./fitness-store";
import { computeAccuracyFromSamples } from "@/lib/sample-line";
import { CardFrame } from "@/components/ui/card";
import { candidateLabel } from "@/lib/candidate-label";
import {
  liveL1Candidates,
  useCycleStream,
  type DashboardSnapshot,
  type LiveCandidate,
} from "@/lib/poll";
import { useSelection } from "@/components/dashboard/SelectionContext";
import {
  correctedFromEvaluators,
  type HistoricalCandidate,
  type HistoricalRound,
} from "./fitness-bars";
import { WhatIfGrid } from "./WhatIfGrid";

interface Props {
  dash: DashboardSnapshot | null;
  dashRound: number | null;
  cycleId: string | null;          // still used for one-shot evaluator-seed scoping
  themeKey: string;
}

export function FitnessPanel({ dash, dashRound, cycleId, themeKey }: Props) {
  // Shared candidate selection — driving any of {fitness bar, lineage stub}
  // sets this context slot; the other surface(s) re-render highlighted.
  // FitnessChart resolves selectedKey → bar index by matching `bar.key`
  // against the SelectedCandidate's {round, candidate_id}. Aliased away
  // from the evaluator-set `selected` already in this component.
  const { selected: selectedCandidate, setSelected: setSelectedCandidate } = useSelection();
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
  const { showComposite, showWhatIf, selected, seededForCycle } = useFitnessState();
  const seeded = seededForCycle != null && seededForCycle === cycleId;
  const setShowComposite = (v: boolean | ((p: boolean) => boolean)) =>
    setFitnessState({ showComposite: typeof v === "function" ? v(showComposite) : v });
  const setShowWhatIf = (v: boolean | ((p: boolean) => boolean)) =>
    setFitnessState({ showWhatIf: typeof v === "function" ? v(showWhatIf) : v });
  const setSelected = (s: Set<string>) => setFitnessState({ selected: s });

  const meta = WHATIF_INLINE_META;

  // ── 1. In-flight candidates from the live dashboard
  const inflightCandidates: LiveCandidate[] = liveL1Candidates(dash);
  const currentRound = dashRound ?? 0;

  // ── 2. Historical rounds — round_NNNN.json files on disk. The
  // CycleStreamProvider owns the cache; the dashboard makes one set of
  // network calls per round change and every consumer (TopStrip,
  // TrendChart, LineageTree, FreqChart, HardSamples*, here) reads the
  // same array.
  const { rounds: historyDocs } = useCycleStream();
  const history: HistoricalRound[] = useMemo(() => {
    const out: HistoricalRound[] = [];
    for (const d of historyDocs) {
      if (typeof d.round !== "number") continue;
      out.push({
        round: d.round,
        candidate_scores: d.candidate_scores as HistoricalCandidate[] | undefined,
        origin_accuracy: d.origin_accuracy,
      });
    }
    return out;
  }, [historyDocs]);

  // ── 3. The applicable evaluator set unions every candidate we plot. The
  // origin row has no evaluators; in-flight stats and historical
  // candidate_scores both carry the full dict.
  const realApplicable = useMemo(() => {
    const set = new Set<string>();
    for (const c of inflightCandidates) {
      for (const k of Object.keys(c.stats?.evaluators ?? {})) set.add(k);
    }
    for (const h of history) {
      for (const c of h.candidate_scores ?? []) {
        for (const k of Object.keys(c.evaluators ?? {})) set.add(k);
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

  const inActive = useMemo(() => {
    let parsed: Set<string> | null = null;
    const top = (dash as { composite_fitness_formula?: string | null } | null)?.composite_fitness_formula;
    if (top) {
      parsed = whatifIdentifiersInFormula(top);
    } else {
      for (const c of inflightCandidates) {
        const f = c.stats?.composite_fitness_formula;
        if (f) { parsed = whatifIdentifiersInFormula(f); break; }
      }
    }
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
  }, [dash, inflightCandidates, viewApplicable]);

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

  // One-shot seed: when the cycle binds applicable evaluators for the first
  // time (or the cycle changes), seed `selected` from the formula's inActive
  // set so the operator opens to "what's actually scored" as the default.
  useEffect(() => {
    if (seeded || viewApplicable.size === 0) return;
    const seed = new Set<string>();
    for (const r of rows) {
      if (r.applicable && inActive.has(r.displayName)) seed.add(r.displayName);
    }
    setFitnessState({ selected: seed, seededForCycle: cycleId });
  }, [seeded, viewApplicable, rows, inActive, cycleId]);

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

  const resetActual = () => {
    const next = new Set<string>();
    for (const a of viewApplicable) {
      if (inActive.has(a)) next.add(a);
    }
    setSelected(next);
  };
  const selectNone = () => setSelected(new Set());
  const selectAll = () => {
    const next = new Set<string>();
    for (const a of viewApplicable) next.add(a);
    setSelected(next);
  };

  const selectableCount = rows.filter((r) => r.applicable).length;

  // ── 4. Assemble the unified bar list:
  //   [origin] + [historical round candidates...] + [in-flight current round]
  const bars: BarSlot[] = useMemo(() => {
    const out: BarSlot[] = [];
    // When selected matches the formula's evaluators, reuse the backend's composite byte-for-byte — recomputing as a mean drifts by float ε and can't represent non-mean formulas.
    const useComposite = setsEqual(selected, inActive);

    // Origin — take from any round file (they all carry origin_accuracy)
    // or fall back to the dash field. No evaluator breakdown for origin.
    // Prefer ``dash.origin`` over ``dash.origin_accuracy``: both are the same
    // scalar at the source, but legacy backend writes left
    // ``origin_accuracy`` at 0.0 while populating ``origin``. The prior
    // ``typeof === number`` check accepted the 0.0 and rendered C0 at 0%.
    let originAccuracy: number | null = null;
    const dashOrigin = (dash as { origin?: number } | null)?.origin;
    if (typeof dashOrigin === "number" && dashOrigin > 0) {
      originAccuracy = dashOrigin;
    } else if (typeof dash?.origin_accuracy === "number" && dash.origin_accuracy > 0) {
      originAccuracy = dash.origin_accuracy;
    } else {
      const firstWithOrigin = history.find(
        (h) => typeof h.origin_accuracy === "number" && (h.origin_accuracy ?? 0) > 0,
      );
      if (firstWithOrigin) originAccuracy = firstWithOrigin.origin_accuracy ?? null;
    }
    // Origin scores the full set every time. Prefer the live dashboard's
    // `origin_samples` (written at INIT:exit, before round 1's file lands so
    // C0 carries a count during the very first round); fall back to a later
    // candidate's `expected_samples` once history is on disk.
    let originSamples: number | null = null;
    const dashOriginSamples = (dash as { origin_samples?: number } | null)?.origin_samples;
    if (typeof dashOriginSamples === "number" && dashOriginSamples > 0) {
      originSamples = dashOriginSamples;
    } else {
      for (const h of history) {
        for (const c of h.candidate_scores ?? []) {
          if (typeof c.expected_samples === "number") { originSamples = c.expected_samples; break; }
        }
        if (originSamples != null) break;
      }
    }
    if (originAccuracy != null || history.length > 0 || inflightCandidates.length > 0) {
      out.push({
        key: "C0",
        label: "C0",
        accuracy: originAccuracy,
        composite: null,
        whatif: null,
        started: originAccuracy != null,
        nSamples: originSamples,
        nExpected: null,
      });
    }

    const historicalRoundsSeen = new Set<number>();
    for (const h of history) {
      const roundNum = Number(h.round ?? 0);
      historicalRoundsSeen.add(roundNum);
      const cands = h.candidate_scores ?? [];
      cands.forEach((c, i) => {
        const ev = c.evaluators ?? {};
        const acc = typeof c.accuracy === "number" ? c.accuracy : null;
        const comp = typeof c.composite_fitness === "number" ? c.composite_fitness : null;
        out.push({
          key: `R${roundNum}.${i}`,
          label: c.label || candidateLabel(roundNum, i),
          accuracy: acc,
          composite: comp,
          whatif: useComposite && comp != null ? comp : correctedFromEvaluators(ev, selected, rows),
          started: acc != null || comp != null || Object.keys(ev).length > 0,
          nSamples: typeof c.scored_samples === "number" ? c.scored_samples : null,
          nExpected: typeof c.expected_samples === "number" ? c.expected_samples : null,
          candidateId: c.candidate_id ?? `r${roundNum}_${i}`,
          round: roundNum,
          isWinner: !!c.is_winner,
        });
      });
    }

    // In-flight: only if the current round hasn't yet been written to disk.
    if (
      inflightCandidates.length > 0 &&
      !historicalRoundsSeen.has(currentRound)
    ) {
      for (const c of inflightCandidates) {
        const i = Number(c.idx);
        if (!Number.isFinite(i) || i < 0) continue;
        const ev = c.stats?.evaluators ?? {};
        const acc = typeof c.stats?.accuracy === "number"
          ? c.stats.accuracy
          : computeAccuracyFromSamples(c.samples);
        const comp = typeof c.stats?.composite_fitness === "number"
          ? c.stats.composite_fitness
          : null;
        out.push({
          key: `live.${currentRound}.${i}`,
          label: c.label || candidateLabel(currentRound, i),
          accuracy: acc,
          composite: comp,
          whatif: useComposite && comp != null ? comp : correctedFromEvaluators(ev, selected, rows),
          started:
            (c.samples?.length ?? 0) > 0 ||
            typeof c.stats?.accuracy === "number" ||
            typeof c.stats?.composite_fitness === "number" ||
            Object.keys(ev).length > 0,
          nSamples: c.samples?.length ?? null,
          nExpected: null,
          candidateId: `r${currentRound}_${i}`,
          round: currentRound,
          isWinner: false,
        });
      }
    }

    return out;
  }, [dash, history, inflightCandidates, selected, rows, currentRound, inActive]);

  return (
    <CardFrame
      className={`fitness-card${showWhatIf ? " whatif-open" : ""}`}
      title={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span>Per-candidate fitness</span>
          <span className="badge" title="Bars span every round, origin first; chip = current optimization round">R{currentRound}</span>
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
        </div>
      }
    >
      {(showComposite || showWhatIf) && (
        <div className="fitness-legend">
          <span><span className="dot accuracy" />accuracy</span>
          {showComposite && <span><span className="dot composite" />composite</span>}
          {showWhatIf && <span><span className="dot whatif" />what-if</span>}
        </div>
      )}
      <div className="fitness-body">
      <FitnessChart
        bars={bars}
        showComposite={showComposite}
        showWhatIf={showWhatIf}
        themeKey={themeKey}
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
            setSelectedCandidate(null);
            return;
          }
          setSelectedCandidate({
            round: bar.round,
            candidate_id: bar.candidateId,
            label: bar.label,
            accuracy: bar.accuracy,
            is_winner: !!bar.isWinner,
          });
        }}
      />
      {showWhatIf && (
        <WhatIfGrid
          rows={rows}
          selected={selected}
          inActive={inActive}
          selectableCount={selectableCount}
          bars={bars}
          onToggle={toggle}
          onResetActual={resetActual}
          onSelectNone={selectNone}
          onSelectAll={selectAll}
        />
      )}
      </div>
    </CardFrame>
  );
}
