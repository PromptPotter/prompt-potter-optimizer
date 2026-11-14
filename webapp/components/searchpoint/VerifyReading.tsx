import {
  type MeasuredUnit,
  type VerifyReading as Reading,
  VERIFY_STRATEGY_LABELS,
} from "@/lib/api/types";
import { cx } from "@/lib/cx";
import {
  fmtLevelBand,
  fmtLift,
  fmtLiftInterval,
  fmtSigned,
  fmtUnit,
  unitCount,
} from "@/lib/format";
import { Term } from "@/components/ui";
import { PairedLift } from "@/components/searchpoint/PairedLift";

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
            <Term content="The fitness on the cells this candidate's round bought, then on the fresh cells alone with their 95% band. Different cells, so the step between them is not a paired difference.">
              fitness
            </Term>
          </dt>
          <dd>
            {fmtUnit("score", recorded.fitness?.value)} →{" "}
            <strong>
              {fresh.fitness ? fmtLevelBand("score", fresh.fitness) : fmtUnit("score", null)}
            </strong>{" "}
            ({fmtSigned(reading.fitness_increment, 2)})
          </dd>
        </div>
        <div>
          <dt>
            <Term content="The same two sets of cells under the cycle's scoring formula, which charges cost and length — so it is never the change in the hit rate.">
              composite
            </Term>
          </dt>
          <dd>
            {fmtUnit("score", recorded.composite?.value)} →{" "}
            <strong>{fmtUnit("score", fresh.composite?.value)}</strong> (
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
              {({ estimate, measurand }, shared) => (
                <>
                  <strong>{fmtLift(measurand.unit, estimate.value)}</strong>{" "}
                  {fmtLiftInterval(measurand.unit, estimate.ci_lo, estimate.ci_hi)} on {shared} shared
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
