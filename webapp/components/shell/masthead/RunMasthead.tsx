"use client";
import { Badge, CopyButton, Term, VendorLogo } from "@/components/ui";
import { BenchAction } from "@/components/shell/masthead/BenchAction";
import { CampaignSwitcher } from "@/components/shell/masthead/CampaignSwitcher";
import { PairedLift } from "@/components/shell/PairedLift";
import { CriterionLine } from "@/components/shell/scoring/Criterion";
import { SpendBuckets } from "@/components/shell/SpendBuckets";
import {
  benchStat,
  budgetAxis,
  capLines,
  fmtLift,
  ratePricedText,
  readSpend,
  spendHeadline,
  tagValue,
} from "@/lib/derivations";
import { cx } from "@/lib/cx";
import { fmtAgo, shortId, shortModel } from "@/lib/format";
import { useServedCriterion } from "@/lib/hooks/useServedCriterion";
import { pathLeaf } from "@/lib/ids";
import { useViewedCourse } from "@/lib/lineage";
import { measuringArm, useCycleStream } from "@/lib/poll";
import { useCampaign } from "@/lib/registry";
import { statusTone } from "@/lib/run-phase";
import { TERMS } from "@/lib/terms";
import { useWorkspace } from "@/lib/workspace";

export function RunMasthead() {
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
  const phaseLabel = status ? status.label : "—";
  const phaseTone = status && statusTone(status);
  const phaseBody = (
    <>
      <span className="phase-dot" aria-hidden="true" />
      {phaseLabel}
    </>
  );

  const best = course?.facts.standing ?? null;
  const bench = benchStat(course);
  const round = dash?.round_axis.position ?? null;
  const roundsCap = dash ? budgetAxis(dash.run_limits.budget, "rounds") : null;
  const position = measuringArm(dash)?.label ?? (round != null ? `R${round}` : "—");

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
          {campaign && <Badge>{tagValue(campaign, "dataset")}</Badge>}
          {campaign?.facts.label && <span className="run-name">{campaign.facts.label}</span>}
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
              campaign.stats.find((s) => s.key === "rounds")?.value,
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
              "—"
            )}
          </Term>
          {/* Ahead of the chip it fills: an ungraded bench's reason runs the row past the viewport. */}
          {viewedPath && !innerLeaf && <BenchAction path={viewedPath} />}
          <span className="chip">
            <span className="chip-lbl">Bench</span>
            {bench?.value ?? "—"}
            {bench?.sub && <span className="chip-of"> {bench.sub}</span>}
          </span>
          <span className="chip">
            <span className="chip-lbl">Rounds</span>
            {position}
            {roundsCap != null && <span className="chip-of"> / {roundsCap}</span>}
          </span>
          <span className={cx("chip", metered?.bill_is_floor && "chip-warn")}>
            <span className="chip-lbl">Spend</span>
            {metered ? (
              <Term content={<SpendBuckets metered={metered} campaignUsd={spend.campaignUsd} />}>
                {spendHeadline(metered)}
              </Term>
            ) : (
              "—"
            )}
            {priced && <span className="chip-of"> + {priced}</span>}
            {caps.map((line) => (
              <span key={line} className="chip-of">
                {" "}
                · {line}
              </span>
            ))}
          </span>
        </div>
      </div>
    </header>
  );
}
