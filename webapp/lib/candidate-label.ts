// Fallback only: display sites read the served `label` verbatim. Round 0 has one arm, so idx>0
// there is a broken contract and renders `C0.n!` rather than colliding on "C0".

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

// Browser-local routing key for a still-scoring candidate: no Python mints it (`RoundBuffer`
// stores no id), so a served row is never joined on one. No caller hand-builds the string.
export function liveCandidateId(
  round: number | null | undefined,
  idx: number | null | undefined,
): string {
  return `r${round}_${idx}`;
}
