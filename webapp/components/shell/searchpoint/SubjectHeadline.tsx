"use client";
import { ratePricedText, spendHeadline } from "@/lib/derivations";
import type { ArmReading, MeteredSpend, PairedReading } from "@/lib/api/types";
import { fmtPct0 } from "@/lib/format";
import { HoverCard, Term } from "@/components/ui";
import { PairedLift } from "@/components/shell/PairedLift";
import { SpendBuckets } from "@/components/shell/SpendBuckets";

export function SubjectHeadline({
  metered,
  reading,
  bench,
}: {
  metered: MeteredSpend | null;
  reading: ArmReading | null;
  // served: `bench_score.vs_origin`, set only where the shown searchpoint is the graded pick.
  bench: PairedReading | null;
}) {
  const priced = metered
    ? ratePricedText(metered.rate_priced_usd, metered.calls_rate_priced)
    : null;
  return (
    <div className="subject-headline">
      {metered ? (
        <>
          <Term content={<SpendBuckets metered={metered} />}>
            <strong>{spendHeadline(metered)}</strong>
          </Term>
          <span className="subject-headline-unit">
            billed{priced ? ` + ${priced}` : ""}
          </span>
        </>
      ) : (
        <strong>—</strong>
      )}
      <span className="subject-headline-sep" aria-hidden="true">
        ·
      </span>
      <Lift reading={reading} bench={bench} />
    </div>
  );
}

function Lift({ reading, bench }: { reading: ArmReading | null; bench: PairedReading | null }) {
  const benchLift = bench ? (
    <>
      bench lift <PairedLift reading={bench} facet="lift" unread="label" />
    </>
  ) : null;
  const accuracy = reading?.own?.accuracy?.value;
  if (reading == null || accuracy == null) {
    return <span className="subject-headline-lift">{benchLift ?? "bench lift —"}</span>;
  }
  const { scored, expected, cut } = reading.panel;
  return (
    <HoverCard
      className="subject-lift-card"
      content={
        <>
          {benchLift ? (
            <>
              <p className="subject-lift-bench">{benchLift}</p>
              <p className="subject-lift-note">
                The pick over the origin in composite fitness, on held-out rows no optimizer node
                read — the same reading for every optimizer.
              </p>
            </>
          ) : null}
          <p className="subject-lift-note">This candidate on the rows it measured.</p>
          {cut ? (
            <p className="subject-lift-note">
              Measurement stopped at {scored} of the round&rsquo;s {expected} samples — a
              partial panel, not a verdict.
            </p>
          ) : null}
        </>
      }
    >
      <span className="subject-headline-acc" tabIndex={0}>
        {fmtPct0(accuracy)}
        {cut ? (
          <span className="subject-headline-cut">
            {" "}
            {scored}/{expected}
          </span>
        ) : null}
      </span>
    </HoverCard>
  );
}
