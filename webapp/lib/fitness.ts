// The client-side fitness picks, in one place. Mirrors the backend rule-owner
// (`display_fitness` in promptpotter/domain/rendering.py).
//
// The bare `number | null` fields stay as they are: a candidate's accuracy,
// composite, and cumulative values are distinct (metric, basis) cells the trend,
// the tiles, and the paired PoBB each genuinely need — NOT copies to collapse. What
// lived scattered was the *rule for picking among them*; that lives here once instead
// of being re-inlined (`?? accuracy`, `is_winner ? cumulative : …`) at every read site.
// The metric selector itself is `HeadlineMetric` (lib/derivations/headline-stats.ts),
// already single-source — not re-declared here.

/**
 * A candidate's painted value under the accuracy basis: the round winner shows the
 * cumulative frontier (the cross-round-comparable lineage spine), a loser its own
 * per-round subset accuracy.
 */
export function accuracyBasisValue(
  isWinner: boolean,
  cumulative: number | null | undefined,
  subset: number | null | undefined,
): number | null {
  return (isWinner ? cumulative : null) ?? subset ?? null;
}
