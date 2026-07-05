"use client";
// Bar-row assembly for FitnessPanel. Decorates the shared candidate
// spine (`useRoundCandidates`) with what-if + diagnostic overlays so
// the panel itself stays pure render. The spine lookup runs once per
// dash snapshot via the hook's memo, so the work-in-this-memo is a
// thin map() over already-resolved rows.

import { useMemo } from "react";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { accuracyOverSampleSet } from "./fitness-bars";
import { samplesForRow } from "@/lib/derivations";
import type { BarSlot } from "./FitnessChart";
import type { DiagnosticRunRecord } from "@/lib/api";
import type { DashboardSnapshot } from "@/lib/poll";

export function useFitnessBars(
  diagByLabel: Map<string, DiagnosticRunRecord>,
  // Fixed-sample-set mode: when non-null, every bar's accuracy is over exactly these
  // sample ids (∩ the samples that candidate ran), so all candidates compare on one
  // basis. Closed candidates read the served scorer-faithful value
  // (`sampleSetByCandidate`); the in-flight round (no round file yet) live-slices `dash`.
  sampleSet: number[] | null,
  sampleSetByCandidate: ReadonlyMap<string, { accuracy: number | null; n: number }>,
  dash: DashboardSnapshot | null,
  // The What-If bar value, served by the backend under the active `score:` lens
  // (the lineage overlay's `lensValueByCandidate`) — NOT recomputed here (R-36).
  // Keyed `{cycle_id}::{candidate_id}`; `cycleId` scopes the lookup to the bars'
  // own cycle. Empty map / absent key ⇒ null (no What-If lens, or unscorable).
  whatifByCandidate: ReadonlyMap<string, number>,
  cycleId: string | null,
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
      // What-If value comes from the served lens projection (one backend scoring
      // operation over the candidate's stored evaluator namespace), looked up by the
      // same candidate identity the bars and the lineage tree share. null when no
      // `score:` lens is active or the candidate is unscorable under it — including
      // origin (round 0), whose namespace can't satisfy an evaluator formula.
      const whatif =
        cycleId == null ? null : (whatifByCandidate.get(`${cycleId}::${row.candidate_id}`) ?? null);

      if (sliceIds) {
        // Accuracy over the chosen sample subset, candidate-by-candidate (never
        // merged across rounds). Closed candidates read the served scorer-faithful
        // value (the lineage `samples=` lens already re-scored it); the in-flight
        // round has no round file yet, so it live-slices the dash HIT/MISS lines.
        // Composite / what-if are per-round aggregates that can't be re-sliced per
        // sample, so they're suppressed here — the chart shows sliced accuracy alone.
        const served =
          cycleId != null && row.source === "history"
            ? sampleSetByCandidate.get(`${cycleId}::${row.candidate_id}`)
            : undefined;
        const sliced =
          served ?? accuracyOverSampleSet(samplesForRow(row, dash, null), sliceIds);
        return {
          key: row.key,
          label: row.label,
          accuracy: sliced.accuracy,
          composite: null,
          whatif: null,
          // θ is a per-round election-fit aggregate — can't be re-sliced per sample,
          // so it's suppressed in slice mode just like composite / what-if.
          theta: null,
          thetaSe: null,
          compositeCiLo: null,
          compositeCiHi: null,
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
        theta: row.theta,
        thetaSe: row.theta_se,
        compositeCiLo: row.compositeCiLo,
        compositeCiHi: row.compositeCiHi,
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
  }, [candidates, diagByLabel, sampleSet, sampleSetByCandidate, dash, whatifByCandidate, cycleId]);
}
