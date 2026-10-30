"use client";
import { Badge, CopyButton, Term, VendorLogo } from "@/components/ui";
import { BenchAction } from "@/components/shell/masthead/BenchAction";
import { CampaignSwitcher } from "@/components/shell/masthead/CampaignSwitcher";
import { PairedLift } from "@/components/shell/PairedLift";
import { CriterionLine } from "@/components/shell/scoring/Criterion";
import { SpendBuckets } from "@/components/shell/SpendBuckets";
import {
  benchSentence,
  benchWord,
  billText,
  budgetAxis,
  capLines,
  fmtLift,
  ratePricedText,
  readSpend,
  roundsText,
  runWord,
  spendHeadline,
  statusTone,
  tagLabel,
  tagValue,
} from "@/lib/derivations";
import { cx } from "@/lib/cx";
import { fmtAgo, PLACEHOLDER, shortId, shortModel } from "@/lib/format";
import { useServedCriterion } from "@/lib/hooks/useServedCriterion";
import { pathLeaf } from "@/lib/ids";
import { useViewedCourse } from "@/lib/lineage";
import { measuringArm, useCycleStream } from "@/lib/poll";
import { useCampaign, useRegistry } from "@/lib/state/registry";
import { TERMS } from "@/lib/terms";
import { useWorkspace } from "@/lib/workspace";

export function RunMasthead() {
  const { viewedPath, checkin } = useWorkspace();
  // No run viewed is a check-in (`lib/ingest-flow.tsx`): it has no run, so no run chip stands over it.
  return viewedPath === null ? <CheckinMasthead draftId={checkin?.draftId ?? null} /> : <RunHeader />;
}

// An unstarted campaign: what its listing serves — dataset, name, lifecycle word, check-in spend.
function CheckinMasthead({ draftId }: { draftId: string | null }) {
  const campaign = useRegistry().unstarted.find((c) => c.address === draftId);
  if (!campaign) return null;
  const spent = campaign.facts.spend_lifetime;
  return (
    <header className="run-header">
      <div className="run-header-inner">
        <div className="run-title">
          <Badge>{tagValue(campaign, "dataset")}</Badge>
          <span className="run-name">{campaign.label}</span>
        </div>
        <div className="run-chips">
          <span className="chip">
            <span className="chip-lbl">State</span>
            {tagLabel(campaign, "lifecycle")}
          </span>
          <span className="chip">
            <span className="chip-lbl">Spent</span>
            {billText(spent.billed_usd, spent.bill_is_floor)}
          </span>
        </div>
      </div>
    </header>
  );
}

function RunHeader() {
  const { campaignId, leafCycleId, viewedPath, following, followActive } = useWorkspace();

  const { dash } = useCycleStream();
  const course = useViewedCourse();
  const runState = course?.state ?? null;
  const served = useServedCriterion();

  const campaign = useCampaign(campaignId);
  const suffix = campaign && shortId(campaign.address);
  const runsWith = campaign?.facts.runs_with ?? null;
  const innerLeaf = viewedPath && viewedPath.length > 1 ? pathLeaf(viewedPath) : null;

  const status = runState?.status ?? null;
  const phaseLabel = status ? runWord(status) : PLACEHOLDER;
  const phaseTone = status && statusTone(status);
  const phaseBody = (
    <>
      <span className="phase-dot" aria-hidden="true" />
      {phaseLabel}
    </>
  );

  const best = course?.facts.standing ?? null;
  // The bench grades the course holding the line; every other course reads it held elsewhere.
  const bench = course?.facts.bench ?? null;
  const holdsLine = bench !== null || course?.address === campaign?.facts.line;
  const benchPass = bench?.status;
  const benchRows =
    benchPass?.scored != null && benchPass.expected != null && benchPass.scored < benchPass.expected
      ? `${benchPass.scored} of ${benchPass.expected} rows`
      : null;
  const round = dash?.round_axis.position ?? null;
  const roundsCap = dash ? budgetAxis(dash.run_limits.budget, "rounds") : null;
  const position = measuringArm(dash)?.label ?? (round != null ? `R${round}` : PLACEHOLDER);

  const spend = readSpend(dash);
  const { metered } = spend;
  const caps = capLines(spend);
  const priced = metered
    ? ratePricedText(metered.rate_priced_usd, metered.calls_rate_priced)
    : null;

  return (
    <header className="run-header">
      <div className="run-header-inner">
        <div className="run-title">
          {/* One headline: the campaign's label, else the dataset it runs — never a chip alone. */}
          {campaign?.facts.label && (
            <Badge className="run-dataset">{tagValue(campaign, "dataset")}</Badge>
          )}
          {campaign && (
            <span className="run-name">{campaign.facts.label ?? tagValue(campaign, "dataset")}</span>
          )}
          {suffix && suffix !== campaign.address && <span className="run-suffix">__{suffix}</span>}
          <CampaignSwitcher />
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
          {leafCycleId && (
            <span className="run-id">
              ID: {leafCycleId}
              <CopyButton data={leafCycleId} title="Copy this cycle's id" />
            </span>
          )}
        </div>
        {campaign && (
          <div className="run-setup">
            <span className="run-setup-vendors">
              {runsWith?.vendors.map((v) => (
                <VendorLogo key={v.vendor} vendor={v.vendor} models={v.models} />
              ))}
            </span>
            {[
              runsWith ? `${runsWith.optimizer} optimizer` : "pipeline unreadable",
              ...(runsWith?.vendors.flatMap((v) => v.models).map(shortModel) ?? []),
              campaign.facts.line_course && roundsText(campaign.facts.line_course),
              fmtAgo(campaign.span.last_at),
            ]
              .filter(Boolean)
              .join(" · ")}
            {served.formula ? <CriterionLine mask={served.mask} /> : null}
          </div>
        )}
        <div className="run-chips">
          {following ? (
            <span className={cx("chip", phaseTone)}>
              <span className="chip-lbl">State</span>
              {phaseBody}
            </span>
          ) : (
            <button
              type="button"
              className={cx("chip", "chip-btn", phaseTone)}
              onClick={() => followActive("dashboard")}
              aria-label={`${phaseLabel} — pinned to this campaign. Follow the latest launch instead.`}
              title="Pinned to this campaign; other launches leave it on screen. Click to follow the latest launch."
            >
              <span className="chip-lbl">Follow</span>
              {phaseBody}
            </button>
          )}
          <Term className="chip" content={TERMS.masthead_best}>
            <span className="chip-lbl">Best</span>
            {best ? (
              <PairedLift reading={best.vs_origin} unread="label">
                {(lift, cells) => (
                  <>
                    {fmtLift(lift, "rates")}
                    <span className="chip-of"> on {cells}</span>
                  </>
                )}
              </PairedLift>
            ) : (
              PLACEHOLDER
            )}
          </Term>
          {viewedPath && !innerLeaf && <BenchAction path={viewedPath} />}
          <span className="chip">
            <span className="chip-lbl">Bench</span>
            {course ? (
              <Term content={benchSentence(bench, holdsLine)}>{benchWord(bench, holdsLine)}</Term>
            ) : (
              PLACEHOLDER
            )}
            {benchRows && <span className="chip-of"> {benchRows}</span>}
          </span>
          <span className="chip">
            <span className="chip-lbl">Rounds</span>
            {position}
            {roundsCap != null && <span className="chip-of"> / {roundsCap}</span>}
          </span>
          <span className={cx("chip", metered?.bill_is_floor && "chip-warn")}>
            <span className="chip-lbl">Spend</span>
            {metered ? (
              // The figure on the chip; how it splits, what was rate-priced and every cap in the hover.
              <Term
                content={
                  <>
                    <SpendBuckets metered={metered} campaignUsd={spend.campaignUsd} />
                    {priced && <p>+ {priced}</p>}
                    {caps.map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                  </>
                }
              >
                {spendHeadline(metered)}
              </Term>
            ) : (
              PLACEHOLDER
            )}
          </span>
        </div>
      </div>
    </header>
  );
}
