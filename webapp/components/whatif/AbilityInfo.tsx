"use client";

import { Popover } from "@/components/ui";
import type { RoundSummary } from "@/lib/api/types.generated";

// Teaches the one piece of vocabulary the fitness surface can't show as a bar:
// difficulty-adjusted ability θ, the metric the round winner is actually elected
// on. Without this, a θ-elected winner sitting below a higher-accuracy sibling
// reads as a contradiction. Copy is the human-facing twin of the θ/1PL/2PL
// glossary entry (docs/glossary.md) + methods/exploration-exploitation.md — one
// corpus, two audiences; don't fork the explanation, keep them in step.
//
// `model` is null while the ruler is cold — a flat ruler is neither 1PL nor 2PL, so the
// third string is a real state, not a placeholder. Never collapse it into "1PL".
export function AbilityInfo({ model }: { model: RoundSummary["calibration_model"] }) {
  return (
    <Popover
      align="right"
      renderTrigger={({ open, toggle }) => (
        <button
          type="button"
          className={`fitness-chip${open ? " on" : ""}`}
          aria-expanded={open}
          aria-label="How candidates are ranked — explain ability θ"
          onClick={toggle}
          title="What is ability θ? Why a lower-accuracy candidate can win."
        >
          θ ?
        </button>
      )}
    >
      {() => (
        <div className="ability-help">
          <h4>How candidates are ranked</h4>
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
      )}
    </Popover>
  );
}
