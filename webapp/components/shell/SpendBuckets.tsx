"use client";
import { DataTable, Term, type Column } from "@/components/ui";
import type { MeteredSpend } from "@/lib/api";
import {
  CEILING_METER_LABELS,
  PREFIX_STATE_TITLES,
  RATE_PRICED_LABEL,
  type PrefixReading,
} from "@/lib/api/types.generated";
import { replayShareLine, type SpendView } from "@/lib/derivations";
import { fmtTokens, fmtUsd } from "@/lib/format";

interface BucketRow {
  key: string;
  label: string;
  usd: Pick<MeteredSpend, "billed_usd" | "rate_priced_usd" | "incurred_usd">;
  // The provider's prefix cache on this kind's sends; null on the total.
  prefix: PrefixReading | null;
  total: boolean;
}

const usd = (r: BucketRow, n: number) => (r.total ? <strong>{fmtUsd(n)}</strong> : fmtUsd(n));

const COLUMNS: readonly Column<BucketRow>[] = [
  {
    id: "bucket",
    label: "By bucket",
    width: "minmax(10ch, 1fr)",
    cell: (r) =>
      r.total ? (
        <strong>{r.label}</strong>
      ) : (
        <>
          {r.label}{" "}
          {r.prefix?.badge ? (
            <Term content={PREFIX_STATE_TITLES[r.prefix.state]}>{r.prefix.badge}</Term>
          ) : null}
        </>
      ),
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

// " of <cap>" where the axis is capped; nothing where it is not.
const against = (cap: number | null, fmt: (n: number) => string, share = false) =>
  cap === null ? "" : share ? ` of its ${fmt(cap)} share` : ` of ${fmt(cap)}`;

function Fact({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={className}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

const TOTAL = "spend-buckets-total";

// The one breakdown of a cycle's spend: by bucket, then the campaign's and this cycle's against the caps.
export function SpendBuckets({ view }: { view: SpendView }) {
  const { metered, lines } = view;
  if (metered === null) return null;
  const meter = CEILING_METER_LABELS[metered.meter];
  const tokens = view.budgetTokens !== null;
  return (
    <div className="spend-buckets">
      <DataTable
        ariaLabel="Spend by bucket"
        columns={COLUMNS}
        rows={[
          ...lines.map((l) => ({
            key: l.key,
            label: l.label,
            usd: l.kind,
            prefix: l.kind.sent ? l.kind.prefix : null,
            total: false,
          })),
          { key: "total", label: "Total", usd: metered, prefix: null, total: true },
        ]}
        getRowId={(r) => r.key}
      />
      <p className="spend-buckets-replay">{replayShareLine(metered)}</p>
      <dl>
        {view.campaignUsd !== null && (
          <Fact
            className={TOTAL}
            label={`Campaign spend against the campaign cap, every cycle, ${meter}`}
            value={fmtUsd(view.campaignUsd) + against(view.budgetUsd, fmtUsd)}
          />
        )}
        <Fact
          className={TOTAL}
          label={`This cycle’s spend against the campaign cap, ${meter}`}
          value={fmtUsd(metered.metered_usd) + against(view.shareUsd, fmtUsd, true)}
        />
        {tokens && view.campaignTokens !== null && (
          <Fact
            className={TOTAL}
            label="Campaign tokens against the campaign cap, every cycle"
            value={fmtTokens(view.campaignTokens) + against(view.budgetTokens, fmtTokens)}
          />
        )}
        {tokens && (
          <Fact
            label="This cycle’s tokens"
            value={fmtTokens(metered.metered_tokens) + against(view.shareTokens, fmtTokens, true)}
          />
        )}
        {lines
          .filter((l) => l.kind.counted)
          .map((l) => (
            <Fact key={l.key} label={l.label} value={fmtUsd(l.kind.metered_usd)} />
          ))}
        {lines
          .filter((l) => !l.kind.counted)
          .map((l) => (
            <Fact
              key={l.key}
              className="spend-buckets-beside"
              label={`${l.label}, beside the cap`}
              value={fmtUsd(l.kind.metered_usd)}
            />
          ))}
      </dl>
    </div>
  );
}
