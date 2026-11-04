import { DataTable, Term, type Column } from "@/components/ui";
import type { MeteredSpend, ServedDashboard } from "@/lib/api";
import {
  PREFIX_STATE_TITLES,
  RATE_PRICED_LABEL,
  type PrefixReading,
} from "@/lib/api/types.generated";
import { replayShareLine, spendLines } from "@/lib/derivations";
import { fmtUsd } from "@/lib/format";

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

function Fact({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={className}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

// The one breakdown of a cycle's spend: by bucket, then the served metered line with any cap line
// beside it (`ServedDashboard.metered_line` / `cap_lines`, the CLI's stop line), then each kind
// inside the cap or beside it.
export function SpendBuckets({
  metered,
  meterLines,
}: {
  metered: MeteredSpend;
  meterLines: Pick<ServedDashboard, "metered_line" | "cap_lines">;
}) {
  const lines = spendLines(metered);
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
      {[
        ...(meterLines.metered_line === null ? [] : [meterLines.metered_line]),
        ...meterLines.cap_lines,
      ].map((line) => (
        <p key={line} className="spend-buckets-total">
          {line}
        </p>
      ))}
      <dl>
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
