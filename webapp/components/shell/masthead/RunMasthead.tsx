"use client";
import { useMemo } from "react";
import { Badge, CopyButton, Term, VendorLogo } from "@/components/ui";
import { BenchAction } from "@/components/shell/masthead/BenchAction";
import { CampaignSwitcher } from "@/components/shell/masthead/CampaignSwitcher";
import { PairedLift } from "@/components/shell/PairedLift";
import { CriterionLine } from "@/components/shell/scoring/Criterion";
import { SpendBuckets } from "@/components/shell/SpendBuckets";
import {
  benchReading,
  campaignLineParts,
  campaignModels,
  campaignVendors,
  fmtLift,
  ratePricedText,
  readSpend,
  spendHeadline,
} from "@/lib/derivations";
import { CEILING_METER_LABELS } from "@/lib/api/types.generated";
import { cx } from "@/lib/cx";
import { fmtUsd, shortModel } from "@/lib/format";
import { useServedCriterion } from "@/lib/hooks/useServedCriterion";
import { pathLeaf } from "@/lib/ids";
import { measuringLabel, useCycleStream } from "@/lib/poll";
import { useRegistry } from "@/lib/registry";
import { statusTone } from "@/lib/run-phase";
import { TERMS } from "@/lib/terms";
import { useWorkspace } from "@/lib/workspace";

export function RunMasthead() {
  const { campaignId, leafCycleId, viewedPath, following, followActive } = useWorkspace();
  const { forest } = useRegistry();
  const { dash } = useCycleStream();
  const served = useServedCriterion();

  const run = useMemo(
    () =>
      forest.flatMap((o) => o.runs).find((r) => r.campaign.campaign_id === campaignId) ?? null,
    [forest, campaignId],
  );

  const campaign = run?.campaign ?? null;
  const innerLeaf = viewedPath && viewedPath.length > 1 ? pathLeaf(viewedPath) : null;

  const status = dash?.status ?? null;
  const phaseLabel = status ? status.label : "—";
  const phaseTone = status && statusTone(status);
  const phaseBody = (
    <>
      <span className="phase-dot" aria-hidden="true" />
      {phaseLabel}
    </>
  );

  const best = dash?.run_standing ?? null;
  const bench = benchReading(dash?.bench_score);
  const round = dash?.round_axis.position ?? null;
  const roundsCap = dash?.run_limits.max_rounds ?? null;
  const position = measuringLabel(dash) ?? (round != null ? `R${round}` : "—");

  const { metered, budgetUsd } = readSpend(dash);
  const priced = metered
    ? ratePricedText(metered.rate_priced_usd, metered.calls_rate_priced)
    : null;

  return (
    <header className="run-header">
      <div className="run-header-inner">
        <div className="run-title">
          {campaign && <Badge>{campaign.dataset_name}</Badge>}
          {campaign?.label && <span className="run-name">{campaign.label}</span>}
          {campaign?.id_suffix && <span className="run-suffix">__{campaign.id_suffix}</span>}
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
        {run && (
          <div className="run-setup">
            <span className="run-setup-vendors">
              {campaignVendors(run).map((v) => (
                <VendorLogo key={v.vendor} vendor={v.vendor} models={v.models} />
              ))}
            </span>
            {[
              ...(run.campaign.runs_with ? [`${run.campaign.runs_with.optimizer} optimizer`] : []),
              ...campaignModels(run).map(shortModel),
              ...campaignLineParts(run),
            ].join(" · ")}
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
            <span className="chip-lbl">{bench.label}</span>
            {bench.value}
            {bench.sub && <span className="chip-of"> {bench.sub}</span>}
          </span>
          <span className="chip">
            <span className="chip-lbl">Rounds</span>
            {position}
            {roundsCap != null && <span className="chip-of"> / {roundsCap}</span>}
          </span>
          <span className={cx("chip", metered?.bill_is_floor && "chip-warn")}>
            <span className="chip-lbl">Spend</span>
            {metered ? (
              <Term content={<SpendBuckets metered={metered} />}>
                {spendHeadline(metered)}
              </Term>
            ) : (
              "—"
            )}
            {priced && <span className="chip-of"> + {priced}</span>}
            {budgetUsd != null &&
              (metered && !metered.metered_is_bill ? (
                <span className="chip-of">
                  {" "}
                  · {fmtUsd(metered.metered_usd)} / {fmtUsd(budgetUsd)} cap{" "}
                  {CEILING_METER_LABELS[metered.meter]}
                </span>
              ) : (
                <span className="chip-of"> / {fmtUsd(budgetUsd)} cap</span>
              ))}
          </span>
        </div>
      </div>
    </header>
  );
}
