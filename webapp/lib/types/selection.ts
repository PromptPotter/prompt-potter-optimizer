export interface SelectedCandidate {
  // The LEAF hop it was picked on: an arm key is unique only within its cycle.
  cycle_id: string;
  round: number;
  // The served `reading.arm.key`: every join's, and the individual a command addresses.
  arm: string;
  label: string;
}

// Ids and a label only, never a measurement: a selection outlives the poll it was picked on.
export function selectedCandidateOf(
  cycleId: string,
  round: number,
  arm: string,
  label: string,
): SelectedCandidate {
  return { cycle_id: cycleId, round, arm, label };
}

// `cycleId` is the leaf hop the caller READ its rows from; they carry no cycle of their own.
export function isSelectedCandidate(
  sel: SelectedCandidate | null,
  cycleId: string | null,
  round: number,
  arm: string,
): boolean {
  return (
    sel != null &&
    cycleId != null &&
    sel.cycle_id === cycleId &&
    sel.round === round &&
    sel.arm === arm
  );
}
