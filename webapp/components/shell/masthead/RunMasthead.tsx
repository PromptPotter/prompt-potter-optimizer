import type { ReactNode } from "react";
import { Badge, Button, CopyButton, Icon, Said, VendorLogo } from "@/components/ui";
import { Figure, FigureRow } from "@/components/shell/FigureRow";
import { CriterionLine } from "@/components/shell/scoring/Criterion";
import { SpendBuckets } from "@/components/shell/SpendBuckets";
import type { HierarchyCampaign } from "@/lib/api";
import { BRAND } from "@/lib/brand";
import { cx } from "@/lib/cx";
import { readSpend, tagValue, type FigureKey } from "@/lib/derivations";
import { fmtAgo } from "@/lib/format";
import { useServedCriterion } from "@/lib/hooks/useServedCriterion";
import { pathLeaf, type CyclePath } from "@/lib/ids";
import { useViewedCourse } from "@/lib/lineage";
import { useCycleStream } from "@/lib/poll";
import { useRegistry } from "@/lib/state/registry";
import { TERMS } from "@/lib/terms";
import { useViewedListing, useWorkspace } from "@/lib/workspace";

// "Where is this run", over every subject: its title row, and under a run its setup and the viewed
// course's served figures. A phone's title row leads with the way back to the list.
export function RunMasthead() {
  const { viewedPath, leafCycleId, pinned, checkin, navigate, open } = useWorkspace();
  const { running } = useRegistry();
  const listing = useViewedListing();
  const dataset = tagValue(listing, "dataset");
  return (
    <header className={cx("run-header", listing === null && viewedPath === null && "is-bare")}>
      <div className="run-header-inner">
        <div className="run-title">
          <Button
            variant="ghost"
            className={cx("run-back", running.length > 0 && "tone-live")}
            aria-label={running.length > 0 ? `Campaigns — ${running.length} running` : "Campaigns"}
            onClick={() => open("campaigns")}
          >
            <Icon size={20} viewBox="0 0 20 20" strokeWidth={1.6}>
              <path d="M11.5 5 6.5 10l5 5" />
            </Icon>
            {running.length > 0 && <span className="phase-dot" aria-hidden="true" />}
          </Button>
          <span className="run-name">{listing?.label ?? (checkin ? "New campaign" : BRAND.shortName)}</span>
          {/* The dataset as a chip only beside a name of the operator's own. */}
          {dataset !== null && dataset !== listing?.label && <Badge>{dataset}</Badge>}
          {leafCycleId && (
            <span className="run-id">
              ID: {leafCycleId}
              <CopyButton data={leafCycleId} title="Copy this cycle's id" />
            </span>
          )}
          {pinned && (
            <Button
              className="figure-btn"
              onClick={() => navigate(null)}
              aria-label="Follow the latest launch instead of this pinned run"
            >
              Follow
            </Button>
          )}
        </div>
        {viewedPath !== null && (
          <RunFacts path={viewedPath} campaign={listing?.level === "campaign" ? listing : null} />
        )}
      </div>
    </header>
  );
}

function RunFacts({ path, campaign }: { path: CyclePath; campaign: HierarchyCampaign | null }) {
  const { dash } = useCycleStream();
  const course = useViewedCourse();
  const served = useServedCriterion();
  const innerLeaf = path.length > 1 ? pathLeaf(path) : null;
  const spend = readSpend(dash);
  // What the masthead adds to a served figure's Term, by its key; a figure is never a control.
  const details: Partial<Record<FigureKey, ReactNode>> = {
    lift: TERMS.masthead_best,
    spend: spend.metered && <SpendBuckets view={spend} />,
  };

  return (
    <>
      <div className="run-setup">
        {innerLeaf && (
          <span className="inner-breadcrumb">
            <span className="inner-breadcrumb-sep" aria-hidden="true">
              ⤷
            </span>
            <span className="inner-breadcrumb-label" title={innerLeaf.campaignId}>
              inner: {innerLeaf.campaignId}
            </span>
          </span>
        )}
        {campaign && (
          <>
            <span className="run-setup-vendors">
              {campaign.facts.runs_with?.vendors.map((v) => (
                <VendorLogo key={v.vendor} vendor={v.vendor} models={v.models} />
              ))}
            </span>
            {fmtAgo(campaign.span.last_at)}
          </>
        )}
        {served.formula ? <CriterionLine mask={served.mask} /> : null}
        {course?.lines.map((row) => (
          <Said key={row.key} tone={row.tone} label={row.label} sub={row.sub} detail={row.detail} />
        ))}
      </div>
      <FigureRow>
        {course?.figures.map((row) => (
          <Figure key={row.key} row={row} detail={details[row.key]} />
        ))}
      </FigureRow>
    </>
  );
}
