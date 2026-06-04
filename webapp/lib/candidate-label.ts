// Canonical candidate label reader. After the round-numbering rework
// every persisted candidate (in `round_NNNN.json::candidate_scores[]`,
// `dashboard.json::current_round.nodes.l1_score.{input,output}.candidates[]`,
// and snapshot events) carries a `label` field set once at score creation —
// "C0" for origin (round 0), "C{round}.{n}" (n=1..N) for L1 round
// candidates. Display sites read it verbatim.
//
// The fallback computes the same shape from round + idx, used only when a
// caller hands over a partial record during a write-window race or for the
// rare in-flight candidate slot that hasn't been seeded with a label yet.
//
// Defensive: round 0 SHOULD only ever have a single origin candidate
// ("C0"). If we see idx>0 for round 0, the contract is broken (stale
// data from before the canonical-numbering refactor, or a future bug).
// Emit a disambiguated label rather than silently collapsing all bars
// to "C0" — a colliding fallback hides the data-integrity issue.

export function candidateLabel(
  round: number | null | undefined,
  idx: number | null | undefined,
): string {
  const r = Number(round);
  const i = Number(idx);
  if (!Number.isFinite(i) || i < 0) return "C?";
  if (!Number.isFinite(r) || r < 0) return `C${i + 1}`;
  if (r === 0) return i === 0 ? "C0" : `C0.${i + 1}!`;
  return `C${r}.${i + 1}`;
}

// Canonical in-flight candidate id `r{round}_{idx}` — the selection-routing
// id minted backend-side by `_RoundBuffer` for a candidate still scoring (one
// that has no persisted hash id yet). Peer of `candidateLabel`: the id↔label
// pair is the candidate's identity projection, so both live here and no caller
// hand-builds the string. Used to construct ids for in-flight candidates and
// to match a selection against the live candidate slots.
export function liveCandidateId(
  round: number | null | undefined,
  idx: number | null | undefined,
): string {
  return `r${round}_${idx}`;
}
