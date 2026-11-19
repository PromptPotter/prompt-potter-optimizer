import { VendorLogo } from "@/components/ui";
import {
  type HierarchyCampaign,
  type HierarchyCycle,
  type ListedCampaign,
  STATUS_MARK_INFO,
} from "@/lib/api/types";
import { cx } from "@/lib/cx";
import { fmtAgo } from "@/lib/format";
import { figureOf, tagLabel } from "@/lib/derivations";

export function PhaseMark({ entry }: { entry: HierarchyCampaign | HierarchyCycle }) {
  const mark = STATUS_MARK_INFO[entry.state.status.mark];
  return (
    <span
      className={`sidebar-tree-mark tone-${mark.tone}`}
      role="img"
      aria-label={figureOf(entry, "state")?.value ?? undefined}
    >
      {mark.glyph}
    </span>
  );
}

export function CampaignRowLabel({ campaign }: { campaign: ListedCampaign }) {
  const vendors = campaign.tags.filter((t) => t.kind === "vendor");
  const started = campaign.entry_kind === "campaign" ? campaign : null;
  // An unstarted campaign has no run: its lifecycle word stands where the rounds would.
  const rounds =
    started === null ? tagLabel(campaign, "lifecycle") : figureOf(started, "rounds")?.value;
  const sub = [rounds, fmtAgo(campaign.span.last_at)].filter(Boolean).join(" · ");
  return (
    <span className="sidebar-tree-row">
      <span className="sidebar-tree-name sidebar-tree-name-split">
        {vendors.length > 0 && (
          <span className="sidebar-tree-vendors">
            {vendors.map((v) => (
              <VendorLogo key={v.value} vendor={v.value} />
            ))}
          </span>
        )}
        <span className="sidebar-tree-name-head">{campaign.label}</span>
      </span>
      <span className="sidebar-tree-meta sidebar-tree-meta-marked">
        {started && <PhaseMark entry={started} />}
        <span className="sidebar-tree-spend">{figureOf(campaign, "spend_lifetime")?.value}</span>
      </span>
      {sub && (
        <span
          className={cx("sidebar-tree-sub", vendors.length > 0 && "sidebar-tree-sub-indent")}
          title={sub}
        >
          {sub}
        </span>
      )}
    </span>
  );
}
