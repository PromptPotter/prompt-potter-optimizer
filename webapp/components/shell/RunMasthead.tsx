"use client";
import { useMemo } from "react";
import { Badge, CopyButton, Term, VendorLogo } from "@/components/ui";
import { CampaignSwitcher } from "@/components/shell/CampaignSwitcher";
import { SpendBuckets } from "@/components/shell/SpendBuckets";
import { ViewTabs } from "@/components/shell/ViewTabs";
import {
  METER_WORD,
  benchReading,
  buildForest,
  campaignLineParts,
  campaignModels,
  campaignTitle,
  campaignVendors,
  fitnessTrend,
  headlineStats,
  readSpend,
} from "@/lib/derivations";
import { fmtPct0, fmtUsdCents, shortModel } from "@/lib/format";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { pathLeaf } from "@/lib/ids";
import { isMeasuring } from "@/lib/poll";
import { cx } from "@/lib/cx";
import { runPhaseLabel } from "@/lib/run-phase";
import { TERMS } from "@/lib/terms";
import { useWorkspace } from "@/lib/workspace";
import type { Tab } from "@/lib/view-tab";

// ONE header over every tab, owning "where is this run": no pane below repeats a fact it shows
// (webapp/components/CLAUDE.md § Component conventions).
export function RunMasthead({
  tab,
  onSelectTab,
  onTabIntent,
  onFollowed,
}: {
  tab: Tab;
  onSelectTab: (t: Tab) => void;
  onTabIntent: () => void;
  onFollowed: () => void;
}) {
  const { campaignId, leafCycleId, viewedPath, campaigns, cycles, following, followActive } =
    useWorkspace();
  const { dash, dashRound } = useDashboard();

  const origins = useMemo(() => buildForest(campaigns, cycles), [campaigns, cycles]);
  const run = useMemo(
    () =>
      origins
        .flatMap((o) => o.runs)
        .find((r) => r.campaign.campaign_id === campaignId) ?? null,
    [origins, campaignId],
  );

  const spark = useMemo(() => {
    const ys = fitnessTrend(dash?.rounds).best.filter((y): y is number => y != null);
    if (ys.length < 2) return null;
    const W = 120;
    const H = 26;
    const maxY = Math.max(...ys, 0.01);
    const toX = (i: number) => (i / (ys.length - 1)) * W;
    const toY = (v: number) => H - 2 - (v / maxY) * (H - 4);
    const path = ys
      .map((y, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(y).toFixed(1)}`)
      .join("");
    return { path, area: `${path} L${W},${H} L0,${H} Z`, W, H };
  }, [dash?.rounds]);

  const title = run ? campaignTitle(run.campaign) : null;
  // Backing out is the remote's drill button; this only says where the view is.
  const innerLeaf = viewedPath && viewedPath.length > 1 ? pathLeaf(viewedPath) : null;

  const runPhase = dash?.run_phase ?? null;
  const phaseLabel = runPhaseLabel(runPhase, dash?.stop_reason);
  const phaseBody = (
    <>
      <span className="phase-dot" aria-hidden="true" />
      {phaseLabel}
    </>
  );

  const { best } = headlineStats(dash);
  const bench = benchReading(dash?.bench_score, dash?.run_phase);
  // `dash.candidate` goes stale between rounds, so it stands in only while the measurement works.
  const scoringCand = dash && isMeasuring(dash) ? String(dash.candidate || "").split("/")[0] : "";
  const roundsCap = dash?.run_limits?.max_rounds ?? null;
  const position = scoringCand || (dashRound != null ? `R${dashRound}` : "—");

  const { metered, budgetUsd, unpricedTokens } = readSpend(dash);
  const spendFloor = unpricedTokens > 0 ? "≥" : "";

  return (
    <header className="run-header">
      <div className="run-header-inner">
        <div className="run-title">
          {title && <Badge>{title.dataset}</Badge>}
          {title?.label && <span className="run-name">{title.label}</span>}
          {title?.suffix && <span className="run-suffix">__{title.suffix}</span>}
          <CampaignSwitcher origins={origins} />
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
          </div>
        )}
        {/* Every chip reads `dash` for the VIEWED LEAF, the one per-cycle source. */}
        <div className="run-chips">
          {following ? (
            <span className={cx("chip", `phase-${runPhase}`)}>
              <span className="chip-lbl">State</span>
              {phaseBody}
            </span>
          ) : (
            <button
              type="button"
              className={cx("chip", "chip-btn", `phase-${runPhase}`)}
              onClick={() => {
                followActive();
                onFollowed();
              }}
              aria-label={`${phaseLabel} — pinned to this campaign. Follow the latest launch instead.`}
              title="Pinned to this campaign; other launches leave it on screen. Click to follow the latest launch."
            >
              <span className="chip-lbl">Follow</span>
              {phaseBody}
            </button>
          )}
          <Term className="chip" content={TERMS.masthead_best}>
            <span className="chip-lbl">Best</span>
            {fmtPct0(best)}
            {spark && (
              <svg
                className="run-spark"
                viewBox={`0 0 ${spark.W} ${spark.H}`}
                aria-hidden="true"
              >
                <path className="area" d={spark.area} />
                <path className="line" d={spark.path} />
              </svg>
            )}
          </Term>
          {/* The headline: the selection graded on held-out rows; BEST beside it is the optimizer's own. */}
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
          <span className={cx("chip", unpricedTokens > 0 && "chip-warn")}>
            <span className="chip-lbl">Spend</span>
            {metered ? (
              <Term content={<SpendBuckets metered={metered} />}>
                {`${spendFloor}${fmtUsdCents(metered.usd)}`}
              </Term>
            ) : (
              "—"
            )}
            {budgetUsd != null && (
              <span className="chip-of"> / {fmtUsdCents(budgetUsd)} cap</span>
            )}
            {metered && <span className="chip-of"> {METER_WORD[metered.meter]}</span>}
          </span>
        </div>
        <ViewTabs tab={tab} onSelect={onSelectTab} onIntent={onTabIntent} />
      </div>
    </header>
  );
}
