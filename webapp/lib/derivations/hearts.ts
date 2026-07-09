// Banked lives ("hearts") → pip counts. The ONE place filled/empty is derived.
//
// Three surfaces render hearts (TopStrip, lineage cycle nodes, the chat live segment).
// Each re-deriving `filled`/`empty` from `(hearts, cap)` is how they drift, so they all
// call this instead. Mirrors `presentation/views/render/text.py::_heart_bar` — the CLI
// and the webapp must not disagree about what "nearly dead" looks like.

export type HeartPips = {
  /** Banked lives — rendered as solid hearts. */
  filled: number;
  /** Ceiling minus banked — rendered as hollow slots. `0` when the cap is unknown. */
  empty: number;
  /** Bank is empty: the run stops at this round. Render a skull, not zero hearts. */
  dead: boolean;
};

/**
 * The empty pips ARE the readout. `♥♥♥` alone cannot distinguish healthy-of-four from
 * nearly-dead-of-seven, and when lives governs the budget `max_rounds` is null — so the
 * round counter the hearts replaced carried the only scale there was.
 *
 * `cap` is null for a dashboard written before `run_limits.lives_cap` existed; degrade to
 * a bare count rather than inventing a denominator.
 */
export function heartPips(hearts: number, cap: number | null | undefined): HeartPips {
  if (hearts <= 0) return { filled: 0, empty: 0, dead: true };
  if (cap == null || cap < hearts) return { filled: hearts, empty: 0, dead: false };
  return { filled: hearts, empty: cap - hearts, dead: false };
}

/**
 * The bank as a plain glyph string — `♥♥♥♡♡`, or `💀` at zero, or `""` when lives is off.
 *
 * For surfaces that cannot mount a React component: the lineage cladogram is an `<svg>`,
 * so its cycle label needs text, not HTML spans. Byte-identical to the CLI's
 * `_heart_bar`, which is the point — the terminal and the tree must not disagree.
 */
export function heartsText(
  hearts: number | null | undefined,
  cap: number | null | undefined,
): string {
  if (hearts == null) return "";
  const { filled, empty, dead } = heartPips(hearts, cap);
  return dead ? "💀" : "♥".repeat(filled) + "♡".repeat(empty);
}

/** Accessible label + tooltip. Pairs the pips with words, never colour/shape alone. */
export function heartsLabel(hearts: number, cap: number | null | undefined): string {
  if (hearts <= 0) return "No lives left — the run stops after this round";
  const noun = hearts === 1 ? "life" : "lives";
  return cap == null || cap < hearts
    ? `${hearts} ${noun} left`
    : `${hearts} of ${cap} ${noun} left`;
}
