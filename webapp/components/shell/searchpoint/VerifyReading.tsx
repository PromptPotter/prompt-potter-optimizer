"use client";
// One candidate's latest `verify` — its round's verdict re-read over every measurement the
// cross-cycle archive holds for the same config. Every number is the stored record's.

import type { DiagnosticRunRecord } from "@/lib/api";
import { cx } from "@/lib/cx";
import { ageText, fmtFitness, fmtPct0 } from "@/lib/format";
import { Term } from "@/components/ui";

export function VerifyReading({ run }: { run: DiagnosticRunRecord }) {
  return (
    <div className="verify-reading">
      {/* `held` is SERVED: the producer owns when two measured rates count as equal. */}
      {run.held !== null && (
        <span className={cx("verify-verdict", run.held ? "held" : "dropped")}>
          {run.held ? "held" : "dropped"}
        </span>
      )}
      <dl className="verify-facts">
        <div>
          <dt>
            <Term content="The source campaign's accuracy for this candidate, then the mean hit rate over every measurement the dataset's archive holds for its config.">
              accuracy
            </Term>
          </dt>
          <dd>
            {fmtPct0(run.source_campaign_accuracy)} → <strong>{fmtPct0(run.workspace_accuracy)}</strong>
          </dd>
        </div>
        <div>
          <dt>
            <Term content="The source campaign's composite for this candidate, then the same scorer over every archived measurement of its config.">
              composite
            </Term>
          </dt>
          <dd>
            {fmtFitness(run.source_campaign_composite)} →{" "}
            <strong>{fmtFitness(run.workspace_composite)}</strong>
          </dd>
        </div>
        <div>
          <dt>
            <Term content="Samples this candidate now has measurements for across the dataset's archive, and how many this verify newly measured.">
              samples
            </Term>
          </dt>
          <dd>
            <strong>{run.workspace_n}</strong> (+{run.samples_added} new)
          </dd>
        </div>
        <div>
          <dt>verified</dt>
          <dd>{ageText(run.ts)}</dd>
        </div>
      </dl>
    </div>
  );
}
