"use client";
import type { MeteredSpend } from "@/lib/api";
import {
  METER_WORD,
  billedIncurredBuckets,
  labelledBuckets,
  replayShareLine,
} from "@/lib/derivations";
import { fmtUsd } from "@/lib/format";

// The breakdown every spend figure opens onto: each bucket's bill beside what it incurred, the
// share of the search a replay answered, and what the cap counts of it. Values are served
// (`MeteredSpend`); this only lays them out.
export function SpendBuckets({ metered }: { metered: MeteredSpend }) {
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
          {billedIncurredBuckets(metered).map((b) => (
            <tr key={b.label}>
              <th scope="row">{b.label}</th>
              <td>{fmtUsd(b.billedUsd)}</td>
              <td>{fmtUsd(b.incurredUsd)}</td>
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
          <dd>{fmtUsd(metered.usd)}</dd>
        </div>
        {labelledBuckets(metered.buckets).map((b) => (
          <div key={b.label}>
            <dt>{b.label}</dt>
            <dd>{fmtUsd(b.usd)}</dd>
          </div>
        ))}
        {labelledBuckets(metered.beside).map((b) => (
          <div key={b.label} className="spend-buckets-beside">
            <dt>{b.label}, beside the cap</dt>
            <dd>{fmtUsd(b.usd)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
