// Was a crown EARNED? One rule, every surface that draws one.
//
// The server stamps `is_winner` by identity on `candidate_id` — the round's
// parent, which is a true and useful fact. But round 0 runs exactly one
// candidate (the origin), so C0's crown comes from having nobody to beat. That
// is a fact about the round's SHAPE, not an achievement: badging it "won" puts
// two winners on one course, and the θ-election copy claims an election that
// never ran.
//
// This is not an origin special-case. It is the general predicate "did this
// round have rivals", which round 0 happens to answer no to — and which a
// single-arm round at any offset answers no to for the same reason. It lives
// here rather than at each renderer because a locally-derived fact is one the
// surfaces can disagree about, and these three did.

// Candidate count per round, off any row set carrying a round number.
export function roundSizes(
  rows: readonly { round?: number | null }[],
): ReadonlyMap<number, number> {
  const sizes = new Map<number, number>();
  for (const r of rows) {
    const key = r.round ?? 0;
    sizes.set(key, (sizes.get(key) ?? 0) + 1);
  }
  return sizes;
}

// What a crown means here.
//
//   elected     — won over rivals. The only state the θ-election copy may claim.
//   uncontested — crowned with nobody to beat (round 0, or any single-arm round).
//   none        — no crown. WHY there is none — nothing elected yet vs elected and it
//                 wasn't this one — is `CandidateView.electionPending`, read off whether
//                 any bar in the round carries a crown. It is not a fourth state here:
//                 this predicate sees one candidate and cannot answer a round-level
//                 question, and a `roundClosed` parameter pretending otherwise sat unused
//                 behind its only caller.
export type CrownState = "elected" | "uncontested" | "none";

// `candidatesInRound` is that round's arm count — from `roundSizes` when walking a
// timeline, or the round's own candidate array when you already hold one round.
export function crownState(isWinner: boolean, candidatesInRound: number): CrownState {
  if (!isWinner) return "none";
  return candidatesInRound > 1 ? "elected" : "uncontested";
}

// True only for a crown won over rivals — the badge predicate, unchanged.
export function wasElected(isWinner: boolean, candidatesInRound: number): boolean {
  return crownState(isWinner, candidatesInRound) === "elected";
}
