export interface SelectedCandidate {
  // The LEAF hop it was picked on: a `candidate_id` is unique only within its cycle.
  cycle_id: string;
  round: number;
  candidate_id: string;
  // A join key: the MINTING course's label, not a fork-contributed attempt's renumbered one.
  label: string;
}

// Ids and a label only, never a measurement: a selection outlives the poll it was picked on.
export function selectedCandidateOf(
  cycleId: string,
  round: number,
  candidateId: string,
  label: string,
): SelectedCandidate {
  return { cycle_id: cycleId, round, candidate_id: candidateId, label };
}

// `cycleId` is the leaf hop the caller READ its rows from; they carry no cycle of their own.
export function isSelectedCandidate(
  sel: SelectedCandidate | null,
  cycleId: string | null,
  round: number,
  candidateId: string,
): boolean {
  return (
    sel != null &&
    cycleId != null &&
    sel.cycle_id === cycleId &&
    sel.round === round &&
    sel.candidate_id === candidateId
  );
}
