"use client";

import type { AbilityReading } from "@/lib/api/types.generated";
import type { ThetaCaveat as Caveat } from "@/lib/types";

// Copy is the human-facing twin of docs/methods/verdict-resolution.md — keep the two in step.
// `model` null means a cold ruler (neither 1PL nor 2PL); never collapse it into "1PL".

// SERVED, never derived here (`domain/ruler.py::theta_caveat`, `results.py::is_floor_pinned`),
// so the screen and the optimizer's `confounds` panel cannot disagree.
export const CAVEAT_COPY: Record<Caveat, { head: string; body: string }> = {
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

// Takes the caveat, not the reading: `floor_pinned` rides the candidate's row, the four scale
// caveats ride `RoundResult.ability` — only those carry spans.
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
