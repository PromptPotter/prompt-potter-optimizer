"use client";

import type { MeasuredUnit, VerifyReading as Reading } from "@/lib/api/types";
import { VERIFY_STRATEGY_LABELS } from "@/lib/api/types.generated";
import { cx } from "@/lib/cx";
import { fmtFitness, fmtPct0, fmtSigned, unitCount } from "@/lib/format";
import { Term } from "@/components/ui";
import { PairedLift } from "@/components/shell/PairedLift";

function band(
  v: { ci_lo: number | null; ci_hi: number | null } | null,
  fmt: (n: number) => string,
): string {
  return v?.ci_lo == null || v.ci_hi == null ? "" : ` [${fmt(v.ci_lo)}, ${fmt(v.ci_hi)}]`;
}

export function VerifyReading({ reading, unit }: { reading: Reading; unit: MeasuredUnit }) {
  const { fresh, recorded } = reading;
  return (
    <div className="verify-reading">
      {reading.held !== null && (
        <span className={cx("verify-verdict", reading.held ? "held" : "dropped")}>
          {reading.held ? "held" : "dropped"}
        </span>
      )}
      <dl className="verify-facts">
        <div>
          <dt>
            <Term content="The hit rate on the cells this candidate's round bought, then on the fresh cells alone with their 95% band. Different cells, so the step between them is not a paired difference.">
              accuracy
            </Term>
          </dt>
          <dd>
            {fmtPct0(recorded.accuracy?.value)} → <strong>{fmtPct0(fresh.accuracy?.value)}</strong>
            {band(fresh.accuracy, fmtPct0)} ({fmtSigned(reading.accuracy_increment, 2)})
          </dd>
        </div>
        <div>
          <dt>
            <Term content="The same two sets of cells under the cycle's scoring formula, which charges cost and length — so it is never the change in the hit rate.">
              composite
            </Term>
          </dt>
          <dd>
            {fmtFitness(recorded.composite?.value ?? null)} →{" "}
            <strong>{fmtFitness(fresh.composite?.value ?? null)}</strong> (
            {fmtSigned(reading.composite_increment, 2)})
          </dd>
        </div>
        <div>
          <dt>
            <Term content="This candidate over the campaign origin, paired cell by cell on every cell both scored — the round's and the fresh ones alike. A band spanning zero has not separated the two.">
              lift over origin
            </Term>
          </dt>
          <dd>
            <PairedLift reading={reading.vs_origin} unread="sentence">
              {({ estimate }, shared) => (
                <>
                  <strong>{fmtSigned(estimate.value, 2)}</strong>
                  {band(estimate, (n) => fmtSigned(n, 2))} on {shared} shared
                </>
              )}
            </PairedLift>
          </dd>
        </div>
        <div>
          <dt>
            <Term content="Fresh cells that returned a verdict, and how they were picked. Hardest-first cells sit below the candidate's level by construction, so that read carries no held / dropped call — read the lift.">
              fresh
            </Term>
          </dt>
          <dd>
            <strong>{unitCount(reading.fresh.n, unit)}</strong>,{" "}
            {VERIFY_STRATEGY_LABELS[reading.strategy]}
          </dd>
        </div>
      </dl>
    </div>
  );
}
