// The client-side fitness picks, in one place. Mirrors the backend rule-owner
// (`resolve_fitness_value` in promptpotter/domain/fitness.py); see
// docs/specs/fitness-value-model.md.
//
// The bare `number | null` fields stay as they are: a candidate's accuracy,
// composite, and cumulative values are distinct (metric, basis) cells the trend,
// the tiles, and the paired PoBB each genuinely need — NOT copies to collapse. What
// lived scattered was the *rule for picking among them*; that lives here once instead
// of being re-inlined (`?? accuracy`, `is_winner ? cumulative : …`) at every read site.
// The metric selector itself is `HeadlineMetric` (lib/derivations/headline-stats.ts),
// already single-source — not re-declared here.

/**
 * Composite-or-accuracy: the active-formula composite when present, else accuracy.
 * The backend already resolves this server-side, so this only covers in-flight rows
 * minted before the composite field lands. Mirror of `resolve_fitness_value`.
 */
export function resolveComposite(
  composite: number | null | undefined,
  accuracy: number | null | undefined,
): number | null {
  return composite ?? accuracy ?? null;
}

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
