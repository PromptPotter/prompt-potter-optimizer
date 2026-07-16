// Selection-context shapes.

// The selected candidate. Field names mirror the dashboard.json
// shape (snake_case) so the JSON-side keys stay readable in the
// context and inspector callers don't translate between two
// vocabularies for the same concept.
export interface SelectedCandidate {
  // The cycle that PRODUCED this candidate — the LEAF hop it was picked on. A
  // `candidate_id` is unique only within its cycle, and every surface the
  // selection scopes (inspector, samples, round files) re-roots to the leaf, so
  // the selection has to name the same cycle they read. It is what lets the
  // provider keep a selection made FOR the cycle being navigated to, instead of
  // clearing on any cycle change — which is what made a cross-cycle pick
  // (the sidebar's candidate rows, the forest's off-lane nodes) impossible to
  // write: the click landed and the clear wiped it in the same commit.
  cycle_id: string;
  round: number;
  candidate_id: string;
  label: string;
  accuracy: number | null;
  is_winner: boolean;
}

// Is `sel` the selection FOR this candidate? All THREE fields identify one: a
// `candidate_id` is unique only within its cycle, and a round is only unique within
// one too. The rows the surfaces render carry `(round, candidate_id)` and no cycle —
// the cycle they were READ from is the caller's own fact (the leaf hop it fetched),
// so it names it here rather than have this guess.
//
// Stated once because every surface asks it — the bars, the sidebar's candidate rows,
// the cladogram's nodes, the samples groups — and they had drifted: three of them
// compared round+candidate_id alone and were correct only because the provider clears
// a selection that doesn't name the incoming cycle. That guard is real, but it is one
// policy in one file holding up four call sites that don't state what they rely on.
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
