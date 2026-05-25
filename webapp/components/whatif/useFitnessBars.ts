"use client";
// Bar-row assembly for FitnessPanel. Decorates the shared candidate
// spine (`useRoundCandidates`) with what-if + diagnostic overlays so
// the panel itself stays pure render. The spine lookup runs once per
// dash snapshot via the hook's memo, so the work-in-this-memo is a
// thin map() over already-resolved rows.

import { useMemo } from "react";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { setsEqual, type Row } from "./meta";
import { correctedFromEvaluators } from "./fitness-bars";
import type { BarSlot } from "./FitnessChart";
import type { DiagnosticRunRecord } from "@/lib/api";

export function useFitnessBars(
  selected: Set<string>,
  inActive: Set<string>,
  rows: Row[],
  diagByLabel: Map<string, DiagnosticRunRecord>,
): BarSlot[] {
  const { all: candidates } = useRoundCandidates();
  return useMemo(() => {
    const useComposite = setsEqual(selected, inActive);
    return candidates.map<BarSlot>((row) => {
      const diag = diagByLabel.get(row.label);
      const composite = row.composite;
      // Origin (no evaluators) — the what-if column has no meaningful
      // value, leave it null so the chart skips painting a stub bar.
      const whatif = row.is_origin
        ? null
        : useComposite && composite != null
          ? composite
          : correctedFromEvaluators(row.evaluators, selected, rows);
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
  }, [candidates, selected, rows, inActive, diagByLabel]);
}
