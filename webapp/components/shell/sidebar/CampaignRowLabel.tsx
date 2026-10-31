"use client";
import { VendorLogo } from "@/components/ui";
import type { ListedCampaign } from "@/lib/api";
import { STATUS_MARK_INFO, type RunStatus } from "@/lib/api/types.generated";
import { cx } from "@/lib/cx";
import { fmtAgo } from "@/lib/format";
import { billText, roundsText, runWord, tagLabel } from "@/lib/derivations";

export function PhaseMark({ status }: { status: RunStatus }) {
  const mark = STATUS_MARK_INFO[status.mark];
  return (
    <span className={`unit-library-mark tone-${mark.tone}`} role="img" aria-label={runWord(status)}>
      {mark.glyph}
    </span>
  );
}

export function CampaignRowLabel({ campaign }: { campaign: ListedCampaign }) {
  const vendors = campaign.tags.filter((t) => t.kind === "vendor");
  const started = campaign.level === "campaign" ? campaign : null;
  // An unstarted campaign has no run: its lifecycle word stands where the rounds would.
  const line = started?.facts.line_course ?? null;
  const rounds =
    started === null ? tagLabel(campaign, "lifecycle") : line === null ? null : roundsText(line);
  const spent = campaign.facts.spend_lifetime;
  const sub = [rounds, fmtAgo(campaign.span.last_at)].filter(Boolean).join(" · ");
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
        <span className="unit-library-spend">{billText(spent.billed_usd, spent.bill_is_floor)}</span>
      </span>
      {sub && (
        <span
          className={cx("unit-library-sub", vendors.length > 0 && "unit-library-sub-indent")}
          title={sub}
        >
          {sub}
        </span>
      )}
    </span>
  );
}
