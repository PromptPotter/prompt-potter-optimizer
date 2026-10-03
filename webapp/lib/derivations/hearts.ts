// The ONE place hearts' filled/empty is derived. Mirrors the CLI's
// `presentation/terminal/ansi.py::_heart_bar` — the two must not disagree.

export type HeartPips = {
  filled: number;
  /** `0` when the cap is unknown. */
  empty: number;
  /** The run stops at this round. Render a skull, not zero hearts. */
  dead: boolean;
};

/** The empty pips ARE the readout: under lives `max_rounds` is null, so the cap is the only
 *  scale. A null `cap` degrades to a bare count, never an invented denominator. */
export function heartPips(hearts: number, cap: number | null | undefined): HeartPips {
  if (hearts <= 0) return { filled: 0, empty: 0, dead: true };
  if (cap == null || cap < hearts) return { filled: hearts, empty: 0, dead: false };
  return { filled: hearts, empty: cap - hearts, dead: false };
}

/** Byte-identical to the CLI's `_heart_bar`; `""` when lives is off. */
export function heartsText(
  hearts: number | null | undefined,
  cap: number | null | undefined,
): string {
  if (hearts == null) return "";
  const { filled, empty, dead } = heartPips(hearts, cap);
  return dead ? "💀" : "♥".repeat(filled) + "♡".repeat(empty);
}

/** Pairs the pips with words, never colour/shape alone. */
export function heartsLabel(hearts: number, cap: number | null | undefined): string {
  if (hearts <= 0) return "No lives left — the run stops after this round";
  const noun = hearts === 1 ? "life" : "lives";
  return cap == null || cap < hearts
    ? `${hearts} ${noun} left`
    : `${hearts} of ${cap} ${noun} left`;
}
