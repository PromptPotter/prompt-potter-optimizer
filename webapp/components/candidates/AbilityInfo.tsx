"use client";

import type { RoundSummary } from "@/lib/api/types.generated";

// Teaches the one piece of vocabulary the fitness surface can't show as a bar:
// difficulty-adjusted ability θ, the metric the round winner is actually elected
// on. Without this, a θ-elected winner sitting below a higher-accuracy sibling
// reads as a contradiction. Copy is the human-facing twin of
// docs/methods/verdict-resolution.md — one corpus, two audiences; don't
// fork the explanation, keep them in step.
//
// Content only — no trigger of its own. It's read once and then never again, so it
// had no business owning a permanent toolbar button; it lives inside the card's
// `⋯` menu, behind a disclosure.
//
// `model` is null while the ruler is cold — a flat ruler is neither 1PL nor 2PL, so the
// third string is a real state, not a placeholder. Never collapse it into "1PL".
export function AbilityHelp({ model }: { model: RoundSummary["calibration_model"] }) {
  return (
    <div className="ability-help">
      <p>
        The round winner is elected on <strong>ability θ</strong>, not raw accuracy. θ is
        difficulty-adjusted: clearing a <em>hard</em> sample counts for more than clearing an
        easy one.
      </p>
      <p>
        So a candidate can win with <em>lower</em> accuracy when it cleared the harder samples —
        and two candidates scored on different sample subsets still compare fairly, which raw
        accuracy can&rsquo;t do. θ is shown in each candidate&rsquo;s tooltip and the scoring
        inspector.
      </p>
      <p className="ability-help-calib">
        {model === "2PL" ? (
          <>
            Calibration: <strong>2PL</strong> — this dataset graduated: θ weighs sample
            difficulty <em>and</em> how much signal each sample carries, so a sample that
            separates good candidates from bad counts for more.
          </>
        ) : model === "1PL" ? (
          <>
            Calibration: <strong>1PL (Rasch)</strong> — difficulty only. (2PL, which also weighs
            how much signal each sample carries, graduates per dataset once the data supports
            it.)
          </>
        ) : (
          <>
            Calibration: <strong>not yet calibrated</strong> — too few banked samples to fit a
            difficulty ruler, so θ is plain accuracy on the logit scale. It calibrates itself as
            rounds bank samples.
          </>
        )}
      </p>
    </div>
  );
}
