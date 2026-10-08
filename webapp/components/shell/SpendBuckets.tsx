"use client";
import type { MeteredSpend } from "@/lib/api";
import { METER_WORD, replayShareLine, spendLines } from "@/lib/derivations";
import { fmtUsd } from "@/lib/format";

// The breakdown every spend figure opens onto: each kind's bill beside what it incurred, the
// share of the search a replay answered, and what the cap counts of it. Values are served
// (`MeteredSpend`); this only lays them out.
export function SpendBuckets({ metered }: { metered: MeteredSpend }) {
  const lines = spendLines(metered);
  return (
    <div className="spend-buckets">
      <table>
        <thead>
          <tr>
            <th scope="col">By bucket</th>
            <th scope="col">Billed</th>
            <th scope="col">Incurred</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.key}>
              <th scope="row">{l.label}</th>
              <td>{fmtUsd(l.kind.billed_usd)}</td>
              <td>{fmtUsd(l.kind.incurred_usd)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="spend-buckets-total">
            <th scope="row">Total</th>
            <td>{fmtUsd(metered.billed_usd)}</td>
            <td>{fmtUsd(metered.incurred_usd)}</td>
          </tr>
        </tfoot>
      </table>
      <p className="spend-buckets-replay">{replayShareLine(metered)}</p>
      <dl>
        <div className="spend-buckets-total">
          <dt>Counted against cap, {METER_WORD[metered.meter]}</dt>
          <dd>{fmtUsd(metered.metered_usd)}</dd>
        </div>
        {lines
          .filter((l) => l.kind.counted)
          .map((l) => (
            <div key={l.key}>
              <dt>{l.label}</dt>
              <dd>{fmtUsd(l.kind.metered_usd)}</dd>
            </div>
          ))}
        {lines
          .filter((l) => !l.kind.counted)
          .map((l) => (
            <div key={l.key} className="spend-buckets-beside">
              <dt>{l.label}, beside the cap</dt>
              <dd>{fmtUsd(l.kind.metered_usd)}</dd>
            </div>
          ))}
      </dl>
    </div>
  );
}
