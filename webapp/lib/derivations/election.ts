// Was a crown EARNED? The server stamps `is_winner` on the round's parent, so a single-arm round
// (round 0 always) crowns with nobody to beat. One predicate for every surface that draws a crown.

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

// Only `elected` may be claimed by the θ-election copy. Why a crown is `none` is round-level,
// `CandidateView.electionPending` — never a fourth state here.
export type CrownState = "elected" | "uncontested" | "none";

export function crownState(isWinner: boolean, candidatesInRound: number): CrownState {
  if (!isWinner) return "none";
  return candidatesInRound > 1 ? "elected" : "uncontested";
}

export function wasElected(isWinner: boolean, candidatesInRound: number): boolean {
  return crownState(isWinner, candidatesInRound) === "elected";
}
