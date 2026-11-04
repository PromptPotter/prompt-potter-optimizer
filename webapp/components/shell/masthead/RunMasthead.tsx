import type { ReactNode } from "react";
import { Badge, Button, CopyButton, Icon, Said, VendorLogo } from "@/components/ui";
import { familyOfGlance, openColumns } from "@/components/shell/families";
import { Figure, FigureRow, ViewDoor } from "@/components/shell/FigureRow";
import type { FamilyId } from "@/lib/address";
import { StageFigures } from "@/components/shell/masthead/StageFigures";
import { CriterionLine } from "@/components/shell/scoring/Criterion";
import { SpendBuckets } from "@/components/shell/SpendBuckets";
import type { HierarchyCampaign } from "@/lib/api";
import type { Figure as FigureType } from "@/lib/api/types.generated";
import { BRAND } from "@/lib/brand";
import { cx } from "@/lib/cx";
import { readSpend, tagValue, type FigureKey } from "@/lib/derivations";
import { fmtAgo } from "@/lib/format";
import { useServedCriterion } from "@/lib/hooks/useServedCriterion";
import { pathLeaf, type CyclePath } from "@/lib/ids";
import { useViewedCourse } from "@/lib/lineage";
import { useCycleStream } from "@/lib/poll";
import { useStageView } from "@/lib/prefs/stage";
import { useRegistry } from "@/lib/state/registry";
import { TERMS } from "@/lib/terms";
import { useViewedListing, useWorkspace } from "@/lib/workspace";

// "Where is this run", over every subject: its title row, and under a run its setup and the viewed
// course's served figures, each the door to the view that reads it; any other open view gets a door
// of its own. A phone's title row leads with the way back to the list.
export function RunMasthead() {
  const { viewedPath, leafCycleId, pinned, checkin, navigate, open } = useWorkspace();
  const { running } = useRegistry();
  const listing = useViewedListing();
  const dataset = tagValue(listing, "dataset");
  const [stage] = useStageView();
  return (
    <header
      className={cx(
        "run-header",
        listing === null && viewedPath === null && "is-bare",
        stage && viewedPath !== null && "is-stage",
      )}
    >
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
        {viewedPath === null ? (
          <SubjectDoors />
        ) : stage ? (
          <StageFigures />
        ) : (
          <RunFacts path={viewedPath} campaign={listing?.level === "campaign" ? listing : null} />
        )}
      </div>
    </header>
  );
}

// The open views no served figure is the door of; with one view open there is nowhere to go.
function useUnnamedViews(named: readonly (FamilyId | null)[]): FamilyId[] {
  const columns = openColumns(useWorkspace().views);
  if (columns.length < 2) return [];
  return columns.map((v) => v.family).filter((family) => !named.includes(family));
}

function SubjectDoors() {
  const unnamed = useUnnamedViews([]);
  if (unnamed.length === 0) return null;
  return (
    <FigureRow>
      {unnamed.map((family) => (
        <ViewDoor key={family} family={family} />
      ))}
    </FigureRow>
  );
}

function RunFacts({ path, campaign }: { path: CyclePath; campaign: HierarchyCampaign | null }) {
  const { dash } = useCycleStream();
  const course = useViewedCourse();
  const served = useServedCriterion();
  const innerLeaf = path.length > 1 ? pathLeaf(path) : null;
  const spend = readSpend(dash);
  const figures = course?.figures.map((row) => ({ row, opens: familyOfGlance(row.key) })) ?? [];
  const unnamed = useUnnamedViews(figures.map((f) => f.opens));
  // What the masthead adds to a served figure's Term, by its key.
  const details: Partial<Record<FigureKey, ReactNode>> = {
    lift: TERMS.masthead_best,
    spend: spend.metered && <SpendBuckets view={spend} />,
  };
  // The masthead draws no sub line, so a figure's sub leads its Term.
  const hover = (row: FigureType) => {
    const extra = details[row.key];
    if (!row.sub) return extra;
    return extra ? <>{row.sub}. {extra}</> : row.sub;
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
        {figures.map(({ row, opens }) => (
          <Figure key={row.key} row={row} detail={hover(row)} opens={opens} />
        ))}
        {unnamed.map((family) => (
          <ViewDoor key={family} family={family} />
        ))}
      </FigureRow>
    </>
  );
}
