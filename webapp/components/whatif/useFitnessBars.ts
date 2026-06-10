"use client";
// Bar-row assembly for FitnessPanel. Decorates the shared candidate
// spine (`useRoundCandidates`) with what-if + diagnostic overlays so
// the panel itself stays pure render. The spine lookup runs once per
// dash snapshot via the hook's memo, so the work-in-this-memo is a
// thin map() over already-resolved rows.

import { useMemo } from "react";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { type Row } from "./meta";
import { accuracyOverSampleSet, correctedFromEvaluators } from "./fitness-bars";
import {
  historicalSamplesFor,
  liveSamplesFor,
} from "@/lib/derivations/round-samples";
import type { BarSlot } from "./FitnessChart";
import type { DiagnosticRunRecord } from "@/lib/api";
import type { DashboardSnapshot, RoundFileDoc } from "@/lib/poll";

export function useFitnessBars(
  selected: Set<string>,
  rows: Row[],
  weights: Readonly<Record<string, number>>,
  diagByLabel: Map<string, DiagnosticRunRecord>,
  // Fixed-sample-set mode: when non-null, every bar's accuracy is recomputed
  // over exactly these sample ids (∩ the samples that candidate ran), so all
  // candidates compare on one basis. `chartedDocs` supplies the per-candidate
  // hit matrix for closed rounds; `dash` for the in-flight round.
  sampleSet: number[] | null,
  chartedDocs: ReadonlyMap<number, RoundFileDoc | null>,
  dash: DashboardSnapshot | null,
): BarSlot[] {
  const { all: candidates } = useRoundCandidates();
  return useMemo(() => {
    // `null` = off (default per-candidate accuracy). A non-null set — INCLUDING
    // an empty one — is slice mode: an empty set blanks every bar (n=0), which
    // is the "Off, select one by one" starting state, not the default.
    const sliceIds = sampleSet != null ? new Set(sampleSet) : null;
    return candidates.map<BarSlot>((row) => {
      const diag = diagByLabel.get(row.label);
      const composite = row.composite;
      // Origin (round 0) has no per-evaluator breakdown — the what-if column
      // has no meaningful value, so leave it null when evaluators are empty
      // and the chart skips painting a stub bar. Otherwise always the weighted
      // recompute, so a slider nudge moves the bar (no composite short-circuit).
      const hasEvaluators = Object.keys(row.evaluators).length > 0;
      const whatif =
        !hasEvaluators && composite == null
          ? null
          : correctedFromEvaluators(row.evaluators, selected, rows, weights);

      if (sliceIds) {
        // Recompute accuracy over the chosen sample subset from this
        // candidate's per-sample hits — live (in-flight) vs historical, never
        // merged. Composite / what-if are per-round aggregates that can't be
        // re-sliced per sample, so they're suppressed in this mode: the chart
        // shows the sliced accuracy alone, honestly.
        const samples =
          row.source === "inflight"
            ? liveSamplesFor(dash, row.round, row.candidate_id)
            : historicalSamplesFor(chartedDocs.get(row.round) ?? null, row.round, row.candidate_id);
        const sliced = accuracyOverSampleSet(samples, sliceIds);
        return {
          key: row.key,
          label: row.label,
          accuracy: sliced.accuracy,
          composite: null,
          whatif: null,
          started: sliced.n > 0,
          nSamples: sliced.n,
          // Surface the chosen-set size as the budget so the chart's per-bar
          // "n of N" annotation flags candidates that ran fewer than the set.
          nExpected: sampleSet?.length ?? null,
          candidateId: row.candidate_id,
          round: row.round,
          isWinner: row.is_winner,
          diag: diag
            ? {
                accuracy: diag.workspace_accuracy,
                workspaceN: diag.workspace_n,
                samplesAdded: diag.samples_added,
              }
            : undefined,
        };
      }

      // "Started" mirrors the prior chart logic: any sign of activity —
      // accuracy, composite, evaluators populated, or any sample scored.
      const started =
        row.accuracy != null ||
        composite != null ||
        Object.keys(row.evaluators).length > 0 ||
        (row.n_samples ?? 0) > 0;
      return {
        key: row.key,
        label: row.label,
        accuracy: row.accuracy,
        composite,
        whatif,
        started,
        nSamples: row.n_samples,
        nExpected: row.n_expected,
        candidateId: row.candidate_id,
        round: row.round,
        isWinner: row.is_winner,
        diag: diag
          ? {
              accuracy: diag.workspace_accuracy,
              workspaceN: diag.workspace_n,
              samplesAdded: diag.samples_added,
            }
          : undefined,
      };
    });
  }, [candidates, selected, rows, weights, diagByLabel, sampleSet, chartedDocs, dash]);
}
