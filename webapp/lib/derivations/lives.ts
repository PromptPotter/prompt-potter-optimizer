import type { LivesReading } from "@/lib/api/types";
import { LIVES_WORDS } from "@/lib/api/types.generated";

// served: `domain/run_standing.py::LivesReading`; `bar` / `label` there are this same reading.

export function livesSpent(lives: LivesReading): boolean {
  return lives.left <= 0;
}

/** Banked pips then spent ones; an uncapped bank, or one past its cap, draws only the banked. */
export function livesPips(lives: LivesReading): { banked: number; spent: number } {
  const spent = lives.cap === null || lives.cap < lives.left ? 0 : lives.cap - lives.left;
  return { banked: Math.max(0, lives.left), spent };
}

export function livesBar(lives: LivesReading): string {
  if (livesSpent(lives)) return LIVES_WORDS.empty;
  const { banked, spent } = livesPips(lives);
  return LIVES_WORDS.banked.repeat(banked) + LIVES_WORDS.spent_pip.repeat(spent);
}

export function livesLabel(lives: LivesReading): string {
  if (livesSpent(lives)) return LIVES_WORDS.empty_sentence;
  const noun = lives.left === 1 ? LIVES_WORDS.life : LIVES_WORDS.lives;
  return lives.cap === null || lives.cap < lives.left
    ? `${lives.left} ${noun} left`
    : `${lives.left} of ${lives.cap} ${noun} left`;
}
