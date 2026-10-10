"use client";

import { THETA_CAVEAT_INFO, type AbilityReading } from "@/lib/api/types.generated";
import type { ThetaCaveat as Caveat } from "@/lib/types";

const fmtSpan = (v: number | null) => (v == null ? null : `${v.toFixed(2)} logits`);

export function ThetaCaveatNotice({
  caveat,
  ability,
}: {
  caveat: Caveat | null | undefined;
  ability?: AbilityReading | null;
}) {
  if (!caveat) return null;
  const { head, body } = THETA_CAVEAT_INFO[caveat];
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
          <strong>{THETA_CAVEAT_INFO[caveat].head}</strong> {THETA_CAVEAT_INFO[caveat].body}
        </p>
      ) : null}
    </div>
  );
}
