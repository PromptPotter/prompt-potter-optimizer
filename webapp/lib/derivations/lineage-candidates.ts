// `/lineage` cycle → its candidates, flat and in round order. THE normalizer for
// the settled cross-cycle source, peer of `round-candidates.ts` (which normalizes
// the live single-cycle `dashboard.json`). Two sources, two derivations, one each —
// a second copy of either is the drift the candidate spine exists to prevent.
//
// Every consumer keys a candidate the same way: the served `candidate_id`, else the
// position-derived live id. The lineage tree, the fitness bars and the served lens
// overlay all resolve one candidate one way, so their keys join.

import { candidateLabel, liveCandidateId } from "@/lib/candidate-label";
import type { CampaignLineageCycle } from "@/lib/api";

export interface LineageCandidate {
  candidateId: string;
  // `C0` for the origin, `C{round}.{n}` otherwise — the SAME string the round file,
  // the console and the candidates chart use. Served verbatim; the position fallback
  // covers a partial record mid-write.
  label: string;
  round: number;
  accuracy: number | null;
  compositeFitness: number | null;
  isWinner: boolean;
  // Did this round elect a winner at all? A held round advances nobody, and a
  // consumer that infers "winner = first candidate" crowns an eliminated one.
  advanced: boolean;
  // Did this round have RIVALS? Round 0 runs one candidate — the origin — so its
  // `is_winner` is true by having nobody to beat. That is a fact about the round's
  // shape, not an achievement, and badging C0 "won" beside the candidate that
  // actually won put two winners on one course.
  contested: boolean;
}

export function lineageCandidates(cycle: CampaignLineageCycle): LineageCandidate[] {
  const out: LineageCandidate[] = [];
  for (const r of cycle.rounds) {
    r.candidates.forEach((cand, i) => {
      out.push({
        candidateId: cand.candidate_id || liveCandidateId(r.round, i),
        label: cand.label || candidateLabel(r.round, i),
        round: r.round,
        accuracy: cand.accuracy,
        compositeFitness: cand.composite_fitness,
        isWinner: cand.is_winner,
        advanced: r.advanced,
        contested: r.candidates.length > 1,
      });
    });
  }
  return out;
}

// `candidate_id` → its label, across every cycle of one campaign. What a fork needs
// to name itself: `index.json::fork.from_candidate_id` is an opaque hash, and the
// label it stands for lives on the PARENT's round — a different cycle in the same
// response. One pass, so a fork resolves without re-walking the tree per row.
export function labelByCandidateId(
  cycles: readonly CampaignLineageCycle[],
): ReadonlyMap<string, string> {
  const m = new Map<string, string>();
  for (const c of cycles) {
    for (const cand of lineageCandidates(c)) m.set(cand.candidateId, cand.label);
  }
  return m;
}
