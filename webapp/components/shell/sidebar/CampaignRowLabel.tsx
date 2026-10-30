"use client";
import { VendorLogo } from "@/components/ui";
import type { ListedCampaign } from "@/lib/api";
import type { RunStatus } from "@/lib/api/types.generated";
import { cx } from "@/lib/cx";
import { fmtAgo } from "@/lib/format";
import { STATUS_MARK, tagLabel } from "@/lib/derivations";

export function PhaseMark({ status }: { status: RunStatus }) {
  const mark = STATUS_MARK[status.mark];
  return (
    <span className={`unit-library-mark tone-${mark.tone}`} role="img" aria-label={status.label}>
      {mark.glyph}
    </span>
  );
}

export function CampaignRowLabel({ campaign }: { campaign: ListedCampaign }) {
  const vendors = campaign.tags.filter((t) => t.kind === "vendor");
  const started = campaign.level === "campaign" ? campaign : null;
  // An unstarted campaign has no run: its lifecycle word stands where the rounds would.
  const rounds =
    started === null
      ? tagLabel(campaign, "lifecycle")
      : started.stats.find((s) => s.key === "rounds")?.value;
  const line = [rounds, fmtAgo(campaign.span.last_at)].filter(Boolean).join(" · ");
  return (
    <span className="unit-library-row">
      <span className="unit-library-name unit-library-name-split">
        {vendors.length > 0 && (
          <span className="unit-library-vendors">
            {vendors.map((v) => (
              <VendorLogo key={v.value} vendor={v.value} />
            ))}
          </span>
        )}
        <span className="unit-library-name-head">{campaign.label}</span>
      </span>
      <span className="unit-library-meta unit-library-meta-marked">
        {started && <PhaseMark status={started.state.status} />}
        <span className="unit-library-spend">{campaign.stats[0]?.value}</span>
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
