"use client";

import type { AbilityReading } from "@/lib/api/types.generated";
import type { ThetaCaveat as Caveat } from "@/lib/types";

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

// The five states in which θ is NOT ability. SERVED, never derived here — the backend decides
// (`domain/ruler.py::theta_caveat` for the four scale states, `results.py::is_floor_pinned` for
// the per-arm one) and this only puts it into words, so the screen and the optimizer's own
// `confounds` panel cannot disagree about whether a number means anything. `Record<Caveat, …>` is
// total, so adding a member to the Python enum fails the build here rather than rendering blank.
// One copy, read by the inline notice and the explainer both.
const CAVEAT_COPY: Record<Caveat, { head: string; body: string }> = {
  cold_ruler: {
    head: "θ is not ability yet",
    body: "No difficulty ruler has been fitted, so θ is plain accuracy on the logit scale, read on each candidate's own cells. These θ compare to each other and to nothing else.",
  },
  flat_ruler: {
    head: "θ is not ability here",
    body: "The ruler itself spans almost nothing, so every cell counts the same and θ is accuracy plus a constant. That is the instrument, not this round's draw — no round could have read wider.",
  },
  collapsed_band: {
    head: "θ is not ability this round",
    body: "This round bought a thin slice of a wide ruler. Inside a band that narrow every cell is equally hard, so ranking on θ ranks on accuracy. That is the draw, not the instrument.",
  },
  unmeasured_delta: {
    head: "θ is not ability this round",
    body: "Most of this round's cells share a difficulty the ruler handed to several cells at once — that is its prior, not a reading of any of them: every candidate that ever saw them answered the same way. θ still counts them, and the value they are pinned to moves as the ruler grows, so a θ higher than last round's can be the scale shifting rather than the prompt improving. Compare candidates within this round; don't read the level across rounds.",
  },
  floor_pinned: {
    head: "θ is not ability for this candidate",
    body: "It scored zero on every cell it answered, so the fit had no response to separate ability from the prior and θ settled on the floor the cells imply. Read the lift with the same suspicion: any difference measured against a floor constant reads 0.000 whatever the candidate did.",
  },
};

const fmtSpan = (v: number | null) => (v == null ? null : `${v.toFixed(2)} logits`);

// Silent unless a caveat is live — a warning that renders every round is read as boilerplate by
// the third one, which is the same rule the `confounds` panel keeps on the optimizer's side.
//
// Takes the CAVEAT, not the reading, because the five arrive on two different carriers: four are
// facts about the round's scale and ride `RoundResult.ability`, while `floor_pinned` is a fact
// about one arm and rides that candidate's row. One component either way — the reader's question
// is the same, so a second notice would be the same warning under a second name. The spans are
// optional for the same reason: only the scale caveats have any.
export function ThetaCaveatNotice({
  caveat,
  ability,
}: {
  caveat: Caveat | null | undefined;
  ability?: AbilityReading | null;
}) {
  if (!caveat) return null;
  const { head, body } = CAVEAT_COPY[caveat];
  const round = fmtSpan(ability?.round_span ?? null);
  const ruler = fmtSpan(ability?.ruler_span ?? null);
  return (
    <div className="theta-caveat" role="note">
      <strong>{head}</strong> {body}
      {round && ruler ? (
        <span className="theta-caveat-spans">
          {" "}
          This round&rsquo;s cells span {round}; the ruler spans {ruler}.
        </span>
      ) : null}
    </div>
  );
}

export function AbilityHelp({
  model,
  caveat,
}: {
  model: AbilityReading["calibration_model"];
  caveat?: AbilityReading["caveat"];
}) {
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
      {caveat ? (
        <p className="ability-help-calib">
          <strong>{CAVEAT_COPY[caveat].head}</strong> {CAVEAT_COPY[caveat].body}
        </p>
      ) : null}
    </div>
  );
}
