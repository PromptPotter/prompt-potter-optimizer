"use client";
import type { MeteredSpend } from "@/lib/api";
import { METER_WORD, labelledBuckets } from "@/lib/derivations";
import { fmtUsd } from "@/lib/format";

// The breakdown every spend figure opens onto: the buckets a cap counts, what is metered beside
// them, and the bill. Values are served (`MeteredSpend`); this only lays them out.
export function SpendBuckets({ metered }: { metered: MeteredSpend }) {
  return (
    <div className="spend-buckets">
      <p className="spend-buckets-head">{METER_WORD[metered.meter]}, by bucket</p>
      <dl>
        {labelledBuckets(metered.buckets).map((b) => (
          <div key={b.label}>
            <dt>{b.label}</dt>
            <dd>{fmtUsd(b.usd)}</dd>
          </div>
        ))}
        <div className="spend-buckets-total">
          <dt>Total the cap counts</dt>
          <dd>{fmtUsd(metered.usd)}</dd>
        </div>
        {labelledBuckets(metered.beside).map((b) => (
          <div key={b.label} className="spend-buckets-beside">
            <dt>{b.label}, beside the cap</dt>
            <dd>{fmtUsd(b.usd)}</dd>
          </div>
        ))}
        {metered.meter !== "bill" && (
          <div className="spend-buckets-beside">
            <dt>Billed by providers</dt>
            <dd>{fmtUsd(metered.billed_usd)}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}
