export interface SelectedCandidate {
  // The LEAF hop it was picked on: a `candidate_id` is unique only within its cycle.
  cycle_id: string;
  round: number;
  candidate_id: string;
  label: string;
  accuracy: number | null;
  is_winner: boolean;
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
