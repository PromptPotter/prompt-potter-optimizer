"use client";
import { DataTable, type Column } from "@/components/ui";
import type { MeteredSpend } from "@/lib/api";
import { CEILING_METER_LABELS, RATE_PRICED_LABEL } from "@/lib/api/types.generated";
import { replayShareLine, spendLines } from "@/lib/derivations";
import { fmtUsd } from "@/lib/format";

interface BucketRow {
  key: string;
  label: string;
  usd: Pick<MeteredSpend, "billed_usd" | "rate_priced_usd" | "incurred_usd">;
  total: boolean;
}

const usd = (r: BucketRow, n: number) => (r.total ? <strong>{fmtUsd(n)}</strong> : fmtUsd(n));

const COLUMNS: readonly Column<BucketRow>[] = [
  {
    id: "bucket",
    label: "By bucket",
    width: "minmax(10ch, 1fr)",
    cell: (r) => (r.total ? <strong>{r.label}</strong> : r.label),
  },
  { id: "billed", label: "Billed", width: "9ch", align: "end", cell: (r) => usd(r, r.usd.billed_usd) },
  {
    id: "priced",
    label: RATE_PRICED_LABEL,
    width: "9ch",
    align: "end",
    cell: (r) => usd(r, r.usd.rate_priced_usd),
  },
  {
    id: "incurred",
    label: "Incurred",
    width: "9ch",
    align: "end",
    cell: (r) => usd(r, r.usd.incurred_usd),
  },
];

// `campaignUsd`: served `campaign_metered_usd`, every cycle of the campaign; null on a replay or with no manifest.
export function SpendBuckets({
  metered,
  campaignUsd,
}: {
  metered: MeteredSpend;
  campaignUsd: number | null;
}) {
  const lines = spendLines(metered);
  const meter = CEILING_METER_LABELS[metered.meter];
  return (
    <div className="spend-buckets">
      <DataTable
        ariaLabel="Spend by bucket"
        columns={COLUMNS}
        rows={[
          ...lines.map((l) => ({ key: l.key, label: l.label, usd: l.kind, total: false })),
          { key: "total", label: "Total", usd: metered, total: true },
        ]}
        getRowId={(r) => r.key}
      />
      <p className="spend-buckets-replay">{replayShareLine(metered)}</p>
      <dl>
        {campaignUsd !== null && (
          <div className="spend-buckets-total">
            <dt>Campaign spend against the campaign cap, every cycle, {meter}</dt>
            <dd>{fmtUsd(campaignUsd)}</dd>
          </div>
        )}
        <div className="spend-buckets-total">
          <dt>This cycle&rsquo;s spend against the campaign cap, {meter}</dt>
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
