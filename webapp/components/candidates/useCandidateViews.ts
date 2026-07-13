"use client";
// Candidate-view assembly for the candidates card. Decorates the shared spine
// (`useRoundCandidates`) with the what-if + diagnostic overlays and hands back
// ONE array that feeds both the bar categories and the dendrogram nodes beneath
// them — which is why the two halves of the Sequence view cannot disagree on
// count, order, or label. The spine lookup runs once per dash snapshot inside
// the hook's own memo, so the work here is a thin map() over resolved rows.

import { useMemo } from "react";
import { useRoundCandidates } from "@/lib/hooks/useRoundCandidates";
import { accuracyOverSampleSet } from "./fitness-bars";
import { samplesForRow } from "@/lib/derivations";
import type { DiagnosticRunRecord } from "@/lib/api";
import type { DashboardSnapshot } from "@/lib/poll";
import type { CandidateView } from "@/lib/types";

export function useCandidateViews(
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
  // Keyed `{cycle_id}::{candidate_id}`; `cycleId` scopes the lookup to this
  // cycle. Empty map / absent key ⇒ null (no What-If lens, or unscorable).
  whatifByCandidate: ReadonlyMap<string, number>,
  cycleId: string | null,
): CandidateView[] {
  const { all: candidates } = useRoundCandidates();
  return useMemo(() => {
    // `null` = off (default per-candidate accuracy). A non-null set — INCLUDING
    // an empty one — is slice mode: an empty set blanks every bar (n=0), which
    // is the "Off, select one by one" starting state, not the default.
    const sliceIds = sampleSet != null ? new Set(sampleSet) : null;
    return candidates.map<CandidateView>((row) => {
      const diag = diagByLabel.get(row.label);
      const diagView = diag
        ? {
            accuracy: diag.workspace_accuracy,
            workspaceN: diag.workspace_n,
            samplesAdded: diag.samples_added,
          }
        : undefined;
      // What-If value comes from the served lens projection (one backend scoring
      // operation over the candidate's stored evaluator namespace), looked up by the
      // same candidate identity the bars and the tree share. null when no `score:`
      // lens is active or the candidate is unscorable under it — including origin
      // (round 0), whose namespace can't satisfy an evaluator formula.
      const whatif =
        cycleId == null
          ? null
          : (whatifByCandidate.get(`${cycleId}::${row.candidate_id}`) ?? null);

      if (sliceIds) {
        // Accuracy over the chosen sample subset, candidate-by-candidate (never
        // merged across rounds). Closed candidates read the served scorer-faithful
        // value (the lineage `samples=` lens already re-scored it); the in-flight
        // round has no round file yet, so it live-slices the dash HIT/MISS lines.
        const served =
          cycleId != null && row.source === "history"
            ? sampleSetByCandidate.get(`${cycleId}::${row.candidate_id}`)
            : undefined;
        const sliced =
          served ?? accuracyOverSampleSet(samplesForRow(row, dash, null), sliceIds);
        // Composite / θ / CI / what-if are per-round ELECTION aggregates — they
        // cannot be re-sliced per sample, so slice mode suppresses them rather than
        // showing a number computed on a different basis than the bar beside it.
        return {
          ...row,
          accuracy: sliced.accuracy,
          composite: null,
          theta: null,
          theta_se: null,
          compositeCiLo: null,
          compositeCiHi: null,
          n_samples: sliced.n,
          // Surface the chosen-set size as the budget so the chart's per-bar
          // "n of N" annotation flags candidates that ran fewer than the set.
          n_expected: sampleSet?.length ?? null,
          whatif: null,
          started: sliced.n > 0,
          diag: diagView,
        };
      }

      // "Started" = any sign of activity: accuracy, composite, evaluators
      // populated, or any sample scored.
      const started =
        row.accuracy != null ||
        row.composite != null ||
        Object.keys(row.evaluators).length > 0 ||
        (row.n_samples ?? 0) > 0;
      return { ...row, whatif, started, diag: diagView };
    });
  }, [candidates, diagByLabel, sampleSet, sampleSetByCandidate, dash, whatifByCandidate, cycleId]);
}
