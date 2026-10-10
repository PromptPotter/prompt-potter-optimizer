"use client";
import { VendorLogo } from "@/components/ui";
import type { RunStatus } from "@/lib/api/types.generated";
import { cx } from "@/lib/cx";
import {
  campaignLineParts,
  campaignVendors,
  spendHeadline,
  type RunGroup,
} from "@/lib/derivations";
import { STATUS_MARK } from "@/lib/run-phase";

export function PhaseMark({ status }: { status: RunStatus }) {
  const mark = STATUS_MARK[status.mark];
  return (
    <span className={`unit-library-mark tone-${mark.tone}`} role="img" aria-label={status.label}>
      {mark.glyph}
    </span>
  );
}

export function CampaignRowLabel({ run }: { run: RunGroup }) {
  const vendors = campaignVendors(run);
  const line = campaignLineParts(run).join(" · ");
  return (
    <span className="unit-library-row">
      <span className="unit-library-name unit-library-name-split">
        {vendors.length > 0 && (
          <span className="unit-library-vendors">
            {vendors.map((v) => (
              <VendorLogo key={v.vendor} vendor={v.vendor} models={v.models} />
            ))}
          </span>
        )}
        <span className="unit-library-name-head">{run.campaign.display_name}</span>
      </span>
      <span className="unit-library-meta unit-library-meta-marked">
        <PhaseMark status={run.line.status} />
        <span className="unit-library-spend">{spendHeadline(run.campaign.spend_metered)}</span>
      </span>
      {line && (
        <span
          className={cx("unit-library-sub", vendors.length > 0 && "unit-library-sub-indent")}
          title={line}
        >
          {line}
        </span>
      )}
    </span>
  );
}
