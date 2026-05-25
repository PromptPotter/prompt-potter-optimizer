// Selection-context shapes. PR-A only lifts the existing
// `SelectedCandidate` to its own file; PR-B (WS-4) reworks the
// SelectionContext API around these types (coupled writes, absorbed
// fitness-store toggles).

// Round-axis value carried by the selection context. `null` means
// "follow the live in-flight round" — the implicit default that
// every consumer interprets the same way.
export type RoundAxisValue = number | null;

// The selected candidate. Field names mirror the dashboard.json
// shape (snake_case) so the JSON-side keys stay readable in the
// context and inspector callers don't translate between two
// vocabularies for the same concept.
export interface SelectedCandidate {
  round: number;
  candidate_id: string;
  label: string;
  accuracy: number | null;
  is_winner: boolean;
}
